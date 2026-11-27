import hashlib
import io
import json
import logging
import re
import time
from contextlib import ExitStack, contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import numpy as np
import rasterio
from PIL import Image
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader

from .catalog import STAC, tile_region
from .profiles import collection_summary, profiles
from .transport import RangeBridge, Transport, atomic_write, digest, file_lock
from .visualization import GDAL_OPTIONS, visualize


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", value) or value in {".", ".."}:
        raise ValueError("Identificador inválido")
    return value


def validate_render_request(collection_id, item_id, profile_id, x, y, z, format_, quality, background):
    identifier(collection_id)
    identifier(item_id)
    identifier(profile_id)
    tile_region(x, y, z)
    if format_ not in {'png', 'jpeg'} or not 50 <= quality <= 100 or background not in {'white', 'black'}:
        raise ValueError('Configuração de imagem inválida')


def encode_image(rgba, format_, quality=90, background="white"):
    if (
        format_ not in {"png", "jpeg"}
        or not 50 <= quality <= 100
        or background not in {"white", "black"}
    ):
        raise ValueError("Configuração de imagem inválida")
    image = Image.fromarray(rgba)
    output = io.BytesIO()
    if format_ == "jpeg":
        canvas = Image.new("RGB", image.size, background)
        canvas.paste(image, mask=image.getchannel("A"))
        canvas.save(
            output, format="JPEG", quality=quality, subsampling=2, optimize=True
        )
    else:
        image.save(output, format="PNG")
    return output.getvalue()


class BdcService:
    def __init__(self, root, *, background=False):
        self.root = Path(root)
        self.background = background

    @contextmanager
    def remote(self, *, retry_token=None):
        from .selection_jobs import selection_store
        with ExitStack() as resources:
            store = None
            def coordinator():
                nonlocal store
                if store is None:
                    store = resources.enter_context(selection_store())
                return store
            @contextmanager
            def origin_admission():
                with coordinator().origin_admission(background=self.background) as check:
                    yield check
            transport = Transport(self.root / "http", metadata_ttl=3600, origin_admission=origin_admission,
                                  origin_resumed=lambda timestamp: coordinator().origin_resumed_since(timestamp),
                                  retry_token=retry_token)
            resources.callback(transport.session.close)
            yield transport

    def collections(self):
        with self.remote() as remote:
            return remote.json(STAC + "/collections")["collections"]

    def collection(self, collection_id):
        identifier(collection_id)
        collection = next(
            (c for c in self.collections() if c["id"] == collection_id), None
        )
        if collection is None:
            raise ValueError("Coleção não encontrada")
        return collection

    def catalog(self):
        return {
            "collections": [collection_summary(c) for c in self.collections()],
            "source": STAC,
            "paused": (self.root / "http/data.inpe.br.paused").exists(),
        }

    def search(self, collection_id, bbox, start, end, page=1, limit=12, max_cloud=100):
        identifier(collection_id)
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if start_date > end_date or (end_date - start_date).days > 366:
            raise ValueError("Escolha um intervalo de até 366 dias")
        if (
            len(bbox) != 4
            or not all(np.isfinite(bbox))
            or not -180 <= bbox[0] < bbox[2] <= 180
            or not -85 <= bbox[1] < bbox[3] <= 85
            or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 100
        ):
            raise ValueError("Aproxime o mapa: a área deve ter até 100 graus quadrados")
        if not 1 <= page <= 1000 or not 1 <= limit <= 24 or not 0 <= max_cloud <= 100:
            raise ValueError("Paginação ou filtro inválido")
        self.collection(collection_id)
        query = {
            "collections": collection_id,
            "bbox": ",".join(map(str, bbox)),
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59.999999Z",
            "limit": limit,
            "page": page,
        }
        if max_cloud < 100:
            query["query"] = json.dumps(
                {"eo:cloud_cover": {"lte": max_cloud}}, separators=(",", ":")
            )
        with self.remote() as remote:
            result = remote.json(STAC + "/search?" + urlencode(query))
        features = result.get("features", [])
        return dict(
            items=features,
            page=page,
            next_page=page + 1
            if any(l.get("rel") == "next" for l in result.get("links", []))
            else None,
            matched=result.get(
                "numberMatched", result.get("context", {}).get("matched")
            ),
            returned=len(features),
        )

    def best_images(self, collection, lon, lat, radius, start, end, criterion="cloud"):
        from .local_quality import compare

        with self.remote() as remote:
            return compare(remote, collection, lon, lat, radius, start, end, criterion)

    def select_images(self, params, check, *, retry_token=None):
        from .local_quality import compare_partitioned
        with self.remote(retry_token=retry_token) as remote:
            result = compare_partitioned(remote, params['collection'], params['lon'], params['lat'],
                                         params['radius_m'], params['start'], params['end'],
                                         params['criterion'], check=check)
        for row in result['rows']:
            item = row.pop('item')
            row['datetime'] = item.get('properties', {}).get('datetime')
            row['profiles'] = [profile['id'] for profile in profiles({'id': params['collection']}, item)]
        return result

    def item(self, collection_id, item_id):
        identifier(collection_id)
        identifier(item_id)
        with self.remote() as remote:
            return remote.json(f"{STAC}/collections/{collection_id}/items/{item_id}")

    def detail(self, collection_id, item_id):
        collection = self.collection(collection_id)
        item = self.item(collection_id, item_id)
        if (
            item.get("id") != item_id
            or item.get("collection", collection_id) != collection_id
        ):
            raise ValueError("Identidade da aquisição incompatível com a consulta")
        return {
            "item": item,
            "collection": collection_summary(collection),
            "profiles": profiles(collection, item),
        }

    def read_cached_tile(self, collection_id, item_id, z, x, y, profile_id, format_, quality=90, background='white'):
        validate_render_request(collection_id, item_id, profile_id, x, y, z, format_, quality, background)
        if format_ != 'png':
            return None
        from .offline_tiles import read_tile, tile_key
        started = time.monotonic()
        stored = read_tile(collection_id, item_id, profile_id, z, x, y)
        if stored is None:
            return None
        data, expires_at = stored
        return data, dict(cache='HIT', shared_cache='HIT', ms=(time.monotonic() - started) * 1000,
                          ranges=0, bytes=0, expires_at=expires_at,
                          key=tile_key(collection_id, item_id, profile_id, z, x, y))

    def render(
        self,
        collection_id,
        item_id,
        z,
        x,
        y,
        profile_id,
        format_,
        quality=90,
        background="white",
        *,
        _offline_lookup=True,
        _refresh=False,
    ):
        if _offline_lookup:
            cached = self.read_cached_tile(collection_id, item_id, z, x, y, profile_id, format_, quality, background)
            if cached is not None:
                return cached
            if format_ == 'png':
                from .offline_tiles import render_interactive
                return render_interactive(self, collection_id, item_id, profile_id, z, x, y, quality, background, refresh=_refresh)
        else:
            validate_render_request(collection_id, item_id, profile_id, x, y, z, format_, quality, background)
        detail = self.detail(collection_id, item_id)
        profile = next((p for p in detail["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            raise ValueError("Composição indisponível para esta imagem")
        item = detail["item"]
        from .offline_tiles import renderer_revision
        key = digest(
            dict(
                version=1,
                renderer=renderer_revision(),
                item=item,
                profile=profile,
                xyz=[x, y, z],
                format=format_,
                quality=quality if format_ == "jpeg" else None,
                background=background if format_ == "jpeg" else None,
                gdal=rasterio.__gdal_version__,
            )
        )
        path = self.root / "tiles" / f"{key}.{format_}"
        started = time.monotonic()
        from .tile_validity import make_diagnostics, valid_diagnostics
        context = dict(collection=collection_id, item=item_id, profile=profile_id, z=z, x=x, y=y)
        revision = renderer_revision()
        diagnostic_path = path.with_suffix('.diagnostics.json')
        with file_lock(path.with_suffix(".lock")):
            if path.exists() and not _refresh:
                data = path.read_bytes()
                stats = dict(cache='HIT', ms=(time.monotonic() - started) * 1000,
                             ranges=0, bytes=0, key=key)
                if diagnostic_path.exists():
                    try:
                        document = json.loads(diagnostic_path.read_text())
                    except (OSError, ValueError):
                        document = None
                    if valid_diagnostics(document, context, revision, hashlib.sha256(data).hexdigest()):
                        stats['tile_diagnostics'] = document
                return data, stats
            with self.remote() as remote:
                raw_diagnostics = {}
                rgba = self.render_pixels(remote, item, profile, x, y, z, raw_diagnostics)
                data = encode_image(rgba, format_, quality, background)
                document = make_diagnostics(raw_diagnostics, context, revision, data)
                persist = True
                try:
                    if document is not None:
                        atomic_write(diagnostic_path, json.dumps(document, allow_nan=False).encode('utf-8'))
                    else:
                        diagnostic_path.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning('BDC tile diagnostic persistence unavailable')
                    document = None
                    persist = False
                if persist:
                    atomic_write(path, data)
                stats = dict(
                    cache="MISS",
                    ms=(time.monotonic() - started) * 1000,
                    ranges=sum(
                        bool(e.get("range")) and not e["cache"] for e in remote.events
                    ),
                    bytes=sum(
                        e.get("bytes", 0) for e in remote.events if not e["cache"]
                    ),
                    key=key,
                )
                if document is not None:
                    stats['tile_diagnostics'] = document
        return data, stats

    def render_pixels(self, remote, item, profile, x, y, z, diagnostics=None):
        arrays, masks = [], []
        with RangeBridge(remote) as bridge, rasterio.Env(**GDAL_OPTIONS):
            try:
                for name in profile["bands"]:
                    asset = item["assets"][name]
                    url = asset["href"]
                    if urlsplit(
                        url
                    ).hostname != "data.inpe.br" or "tiff" not in asset.get("type", ""):
                        raise ValueError("Asset não é um COG público do BDC")
                    with Reader(bridge.url(url)) as reader:
                        declared = (
                            asset.get("raster:bands") or asset.get("eo:bands") or [{}]
                        )
                        nodata = declared[0].get("nodata", reader.dataset.nodata)
                        tile = reader.tile(
                            x,
                            y,
                            z,
                            tilesize=256,
                            nodata=nodata,
                            resampling_method="nearest",
                            reproject_method="nearest",
                        )
                        arrays.extend(tile.array.data)
                        masks.extend(~np.ma.getmaskarray(tile.array))
            except TileOutsideBounds:
                if diagnostics is not None:
                    diagnostics.update(stage='outside_asset_bounds', outside_band=name,
                                       bands=list(profile['bands']), width=256, height=256)
                return np.zeros((256, 256, 4), dtype="uint8")
        expected = 2 if profile["ndvi"] else 3
        if len(arrays) != expected:
            raise ValueError("Número de bandas incompatível com a composição")
        if any(np.shape(mask) != (256, 256) for mask in masks):
            raise ValueError('Dimensões da máscara incompatíveis com o tile')
        valid = np.asarray(masks, dtype=bool).all(axis=0)
        if diagnostics is not None:
            diagnostics.update(stage='source_masks_before_visualization', read_complete=True,
                               bands=list(profile['bands']), width=256, height=256,
                               raw_valid_pixels=int(np.count_nonzero(valid)),
                               valid_pixels_by_band=[int(np.count_nonzero(mask)) for mask in masks])
        data = np.asarray(arrays, dtype=float) * profile["scale"] + profile["offset"]
        return visualize(
            data,
            valid,
            profile["min"],
            profile["max"],
            profile["gamma"],
            profile["ndvi"],
        )
