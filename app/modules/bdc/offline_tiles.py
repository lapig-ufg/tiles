import io
import json
import logging
import math
from pathlib import Path

import rasterio

from PIL import Image

from app.cache.sync_cache import SyncTileCache, InvalidTileError, validate_offline_image
from .admission import admission
from .catalog import tile_region
from .profiles import LABELS
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
    stored = cache.read_valid_image(key)
    if stored is not None:
        try:
            _validate_png(stored[0])
        except InvalidTileError:
            return None
    return stored


def read_tile(collection, item, profile, z, x, y):
    key = tile_key(collection, item, profile, z, x, y)
    try:
        with SyncTileCache() as cache:
            return _read(cache, key)
    except Exception:
        logger.warning('Cache compartilhado BDC indisponível')
        return None


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
            with admission(service.root, count=admission_count):
                data, stats = service.render(collection, item, z, x, y, profile, 'png',
                                             _offline_lookup=False, _refresh=force)
            try:
                _validate_png(data)
            except InvalidTileError as exc:
                return dict(status='failed', reason='invalid_tile', key=key,
                            diagnostics=exc.diagnostics)
            cache.set_png(key, data, ttl, lease=lease)
            stored = _read(cache, key)
            lease.ensure_owned()
            if stored is None or stored[0] != data:
                raise IOError('Persistência da imagem BDC não confirmada')
            return dict(status='cached', expires_at=stored[1], key=key, diagnostics=stats)
