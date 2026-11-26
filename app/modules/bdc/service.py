import io
import json
import re
import time
from contextlib import contextmanager
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
    def __init__(self, root):
        self.root = Path(root)

    @contextmanager
    def remote(self):
        transport = Transport(self.root / "http", metadata_ttl=3600)
        try:
            yield transport
        finally:
            transport.session.close()

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
    ):
        tile_region(x, y, z)
        if (
            format_ not in {"png", "jpeg"}
            or not 50 <= quality <= 100
            or background not in {"white", "black"}
        ):
            raise ValueError("Configuração de imagem inválida")
        detail = self.detail(collection_id, item_id)
        profile = next((p for p in detail["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            raise ValueError("Composição indisponível para esta imagem")
        item = detail["item"]
        key = digest(
            dict(
                version=1,
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
        with file_lock(path.with_suffix(".lock")):
            if path.exists():
                return path.read_bytes(), dict(
                    cache="HIT",
                    ms=(time.monotonic() - started) * 1000,
                    ranges=0,
                    bytes=0,
                    key=key,
                )
            with self.remote() as remote:
                rgba = self.render_pixels(remote, item, profile, x, y, z)
                data = encode_image(rgba, format_, quality, background)
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
        return data, stats

    def render_pixels(self, remote, item, profile, x, y, z):
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
                return np.zeros((256, 256, 4), dtype="uint8")
        expected = 2 if profile["ndvi"] else 3
        if len(arrays) != expected:
            raise ValueError("Número de bandas incompatível com a composição")
        data = np.asarray(arrays, dtype=float) * profile["scale"] + profile["offset"]
        return visualize(
            data,
            np.asarray(masks).all(axis=0),
            profile["min"],
            profile["max"],
            profile["gamma"],
            profile["ndvi"],
        )
