import hashlib
import io
import json
import logging
import math
import time
from pathlib import Path

import rasterio

from PIL import Image

from app.cache.sync_cache import SyncTileCache, InvalidTileError, validate_offline_image
from .admission import admission
from .catalog import tile_region
from .profiles import LABELS
from .read_pool import read_pool
from .transport import digest
from .visualization import PALETTE, VIS


RENDER_VERSION = 1
DEFAULT_TTL = 365 * 24 * 3600
logger = logging.getLogger(__name__)


def renderer_revision():
    proposed_path = Path(__file__).parent / 'proposed-visparams.json'
    proposed = json.loads(proposed_path.read_text()) if proposed_path.exists() else None
    return digest(dict(version=RENDER_VERSION, vis=VIS, proposed=proposed,
                       palette=PALETTE, profiles=LABELS, gdal=rasterio.__gdal_version__))


def tile_key(collection, item, profile, z, x, y):
    from .service import identifier
    for value in (collection, item, profile):
        identifier(value)
    tile_region(x, y, z)
    return 'bdc/' + digest(dict(renderer=renderer_revision(), collection=collection, item=item,
                               profile=profile, xyz=[x, y, z], format='png')) + '.png'


def _validate_png(data):
    validate_offline_image(data)
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG':
            raise InvalidTileError('O cache BDC exige uma imagem PNG')


def _read(cache, key):
    stored = cache.read_valid_object(key)
    if stored is not None and not stored[0].startswith(b'\x89PNG\r\n\x1a\n'):
        return None
    return stored


def read_tile(collection, item, profile, z, x, y):
    key = tile_key(collection, item, profile, z, x, y)
    try:
        with read_pool.borrow(SyncTileCache) as cache:
            return _read(cache, key)
    except Exception:
        logger.warning('Cache compartilhado BDC indisponível')
        return None


def render_interactive(service, collection, item, profile, z, x, y, quality=90, background='white', refresh=False):
    from app.cache.errors import TileLockUnavailable
    from .selection_jobs import OriginBusy

    key = tile_key(collection, item, profile, z, x, y)
    result = None
    rendering = False
    validated = False

    def generate():
        return service.render(collection, item, z, x, y, profile, 'png', quality, background,
                              _offline_lookup=False, _refresh=refresh)

    def unavailable(reason):
        data, stats = result
        logger.warning('Cache compartilhado BDC não publicado: %s', reason)
        return data, dict(stats, shared_cache='UNAVAILABLE', shared_cache_reason=reason)

    try:
        with SyncTileCache() as cache:
            with cache.tile_lock(key) as lease:
                started = time.monotonic()
                stored = _read(cache, key)
                if stored is not None:
                    data, expires_at = stored
                    result = data, dict(cache='HIT', ms=(time.monotonic() - started) * 1000, ranges=0, bytes=0, key=key,
                                        expires_at=expires_at, shared_cache='HIT')
                    validated = True
                    return result
                rendering = True
                result = generate()
                data, stats = result
                _validate_png(data)
                validated = True
                cache.set_png(key, data, DEFAULT_TTL, lease=lease)
                stored = _read(cache, key)
                lease.ensure_owned()
                if stored is None or stored[0] != data:
                    raise IOError('Persistência da imagem BDC não confirmada')
                result = data, dict(stats, key=key, expires_at=stored[1], shared_cache='STORED')
                return result
    except InvalidTileError as exc:
        if result is None:
            raise
        reason = exc.diagnostics.get('validation_code', 'invalid_content')
        if reason not in {'transparent_image', 'white_image', 'black_image'}:
            raise
        logger.warning('Imagem BDC não elegível para cache compartilhado: %s', reason)
        data, stats = result
        return data, dict(stats, shared_cache='SKIPPED', shared_cache_reason=reason)
    except TileLockUnavailable as exc:
        if result is None:
            raise OriginBusy('Coordenação de cache BDC indisponível') from exc
        return unavailable(type(exc).__name__)
    except Exception as exc:
        if rendering and result is None:
            raise
        if result is None:
            logger.warning('Coordenação de cache BDC indisponível: %s', type(exc).__name__)
            raise OriginBusy('Coordenação de cache BDC indisponível') from exc
        if not validated:
            raise
        return unavailable(type(exc).__name__)


def warm_tile(service, collection, item, profile, z, x, y, force=False, ttl=DEFAULT_TTL, admission_count=2):
    key = tile_key(collection, item, profile, z, x, y)
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError('TTL deve ser positivo e finito')
    with SyncTileCache() as cache:
        with cache.tile_lock(key) as lease:
            stored = _read(cache, key)
            if stored is not None and not force:
                expires_at = cache.confirm_valid_image(key, lease)
                if expires_at:
                    return dict(status='already_cached', expires_at=expires_at, key=key, diagnostics={})
            from .tile_validity import PROOF_TTL, absence_kind, checked_time, valid_diagnostics
            context = dict(collection=collection, item=item, profile=profile, z=z, x=x, y=y)
            with admission(service.root, count=admission_count):
                for attempt in range(2):
                    data, stats = service.render(collection, item, z, x, y, profile, 'png',
                                                 _offline_lookup=False, _refresh=force or attempt > 0)
                    try:
                        _validate_png(data)
                        break
                    except InvalidTileError as exc:
                        document = stats.get('tile_diagnostics')
                        valid = valid_diagnostics(document, context, renderer_revision(), hashlib.sha256(data).hexdigest())
                        expiry = checked_time(document).timestamp() + min(ttl, PROOF_TTL) if valid else 0
                        fresh = valid and expiry > time.time()
                        if exc.diagnostics.get('validation_code') == 'transparent_image':
                            kind = absence_kind(document) if fresh else None
                            if kind:
                                return dict(status='no_data', reason='bdc_tile_no_data', key=key,
                                            expires_at=expiry, diagnostics=exc.diagnostics,
                                            evidence=dict(document, kind=kind, count=0))
                            if attempt == 0 and stats.get('cache') == 'HIT' and not fresh:
                                continue
                        return dict(status='failed', reason='invalid_tile', key=key,
                                    diagnostics=exc.diagnostics)
            cache.set_png(key, data, ttl, lease=lease)
            stored = _read(cache, key)
            lease.ensure_owned()
            if stored is None or stored[0] != data:
                raise IOError('Persistência da imagem BDC não confirmada')
            return dict(status='cached', expires_at=stored[1], key=key, diagnostics=stats)
