import io

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.modules.bdc.profiles import profiles
from app.modules.bdc.router import create_router
from app.modules.bdc.service import BdcService, encode_image, identifier


def create_app(service):
    app = FastAPI()
    app.include_router(create_router(service), prefix="/api/bdc")
    return app


from app.modules.bdc.transport import ServicePaused


@pytest.mark.parametrize(
    "format_,magic,mode", [("jpeg", b"\xff\xd8", "RGB"), ("png", b"\x89PNG", "RGBA")]
)
def test_actual_codec_and_alpha(format_, magic, mode):
    pixels = np.zeros((256, 256, 4), dtype="uint8")
    pixels[:128] = [240, 40, 40, 255]
    data = encode_image(pixels, format_, 90, "white")
    assert data.startswith(magic)
    image = Image.open(io.BytesIO(data))
    assert image.mode == mode
    if format_ == "jpeg":
        assert image.getpixel((200, 200)) == (255, 255, 255)
    else:
        assert image.getpixel((200, 200)) == (0, 0, 0, 0)


def test_jpeg_background_and_quality_change_bytes():
    pixels = np.zeros((256, 256, 4), dtype="uint8")
    assert encode_image(pixels, "jpeg", 90, "white") != encode_image(
        pixels, "jpeg", 90, "black"
    )
    rng = np.random.default_rng(42)
    pixels = rng.integers(0, 256, (256, 256, 4), dtype="uint8")
    pixels[..., 3] = 255
    assert len(encode_image(pixels, "jpeg", 60)) < len(encode_image(pixels, "jpeg", 95))


@pytest.mark.parametrize(
    "value", ["../x", "x/y", "http://evil", "..", "a?token=x", "a" * 181]
)
def test_identifier_rejects_url_and_traversal(value):
    with pytest.raises(ValueError):
        identifier(value)


def test_profiles_offset_and_missing_bands():
    item = {
        "id": "S2A_MSIL2A_20240819T133141_N0511_R081_T22KFG_20240819T182249",
        "assets": {b: {} for b in ["B04", "B03", "B02", "B08", "B8A", "B11"]},
    }
    result = profiles({"id": "S2_L2A-1"}, item)
    assert len(result) >= 4 and all(p["offset"] == -1000 for p in result)
    assert next(p for p in result if p["id"] == "tvi-ndvi")["ndvi"]
    assert all(p["offset"] == 0 for p in profiles({"id": "S2-16D-2"}, item))
    item["assets"] = {}
    assert profiles({"id": "S2_L2A-1"}, item) == []


def test_landsat_raw_and_cube_scales():
    item = {
        "id": "x",
        "assets": {b: {} for b in ["red", "green", "blue", "nir08", "swir16"]},
    }
    assert profiles({"id": "landsat-2"}, item)[0]["scale"] == 0.0000275
    assert profiles({"id": "landsat-2"}, item)[0]["offset"] == -0.2
    assert profiles({"id": "LANDSAT-16D-1"}, item)[0]["scale"] == 0.0001


def test_zip_is_catalog_only_and_rgb_alias():
    assert (
        profiles(
            {"id": "S2_L1C_BUNDLE-1"},
            {"id": "zip", "assets": {"zip": {"type": "application/zip"}}},
        )
        == []
    )
    assert profiles({"id": "CB4A-WPM-PCA-FUSED-1"}, {"id": "x", "assets": {"tci": {}}})[
        0
    ]["bands"] == ["tci"]


def test_catalog_style_requires_explicit_ranges():
    collection = {
        "id": "AMZ1-WFI-L4-SR-1",
        "properties": {
            "renders": {
                "rgb": {"assets": ["red", "green", "blue"], "rescale": [[0, 3000]] * 3}
            }
        },
    }
    item = {"assets": {k: {"type": "image/tiff"} for k in ["red", "green", "blue"]}}
    assert profiles(collection, item)[0]["max"] == [3000] * 3
    collection["properties"]["renders"]["rgb"].pop("rescale")
    assert profiles(collection, item) == []


def test_cache_separates_codec_and_reuses_png_parameters(tmp_path, monkeypatch):
    service = BdcService(tmp_path)
    calls = []
    profile = {
        "id": "rgb",
        "bands": ["r", "g", "b"],
        "min": [0] * 3,
        "max": [255] * 3,
        "gamma": 1,
        "scale": 1,
        "offset": 0,
        "ndvi": False,
    }
    monkeypatch.setattr(
        service, "detail", lambda *args: {"item": {"id": "x"}, "profiles": [profile]}
    )

    def pixels(*args):
        calls.append(args)
        return np.full((256, 256, 4), 255, dtype="uint8")

    monkeypatch.setattr(service, "render_pixels", pixels)
    args = ("c", "i", 14, 5943, 8963, "rgb")
    a, first = service.render(*args, "jpeg", 90)
    b, hit = service.render(*args, "jpeg", 90)
    assert a == b and first["cache"] == "MISS" and hit["cache"] == "HIT"
    service.render(*args, "jpeg", 80)
    service.render(*args, "png", 90, _offline_lookup=False)
    _, png_hit = service.render(*args, "png", 70, "black", _offline_lookup=False)
    assert png_hit["cache"] == "HIT" and len(calls) == 3


def test_failed_render_never_writes_image(tmp_path, monkeypatch):
    service = BdcService(tmp_path)
    monkeypatch.setattr(
        service,
        "detail",
        lambda *args: {"item": {"id": "x"}, "profiles": [{"id": "rgb"}]},
    )
    monkeypatch.setattr(
        service,
        "render_pixels",
        lambda *args: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    with pytest.raises(RuntimeError):
        service.render("c", "i", 14, 5943, 8963, "rgb", "jpeg")
    assert not list(tmp_path.rglob("*.jpeg"))


class FakeService:
    def read_cached_tile(self, *args):
        return None

    def catalog(self):
        return {"collections": [], "paused": False}

    def detail(self, *args):
        return {"item": {"id": "x"}, "profiles": []}

    def search(self, *args):
        return {"items": [], "page": 1, "next_page": None}

    def render(self, *args):
        return encode_image(
            np.zeros((256, 256, 4), dtype="uint8"), args[6], args[7], args[8]
        ), dict(cache="HIT", ms=1, ranges=0, bytes=0, key="abc")


def test_api_mime_headers_and_validation():
    client = TestClient(create_app(FakeService()))
    result = client.get("/api/bdc/tiles/c/i/14/1/1.jpeg?profile=rgb")
    assert result.status_code == 200 and result.headers["content-type"] == "image/jpeg"
    assert result.content.startswith(b"\xff\xd8") and result.headers["x-cache"] == "HIT"
    bad = client.get("/api/bdc/tiles/c/i/14/1/1.jpeg?profile=rgb&quality=101")
    assert bad.status_code == 422 and bad.headers["cache-control"] == "no-store"
    assert client.get("/api/bdc/health").json()["service"] == "bdc"


def test_origin_pause_returns_noncacheable_503():
    service = FakeService()
    service.catalog = lambda: (_ for _ in ()).throw(ServicePaused("paused"))
    response = TestClient(create_app(service)).get("/api/bdc/collections")
    assert (
        response.status_code == 503 and response.headers["cache-control"] == "no-store"
    )


def test_warm_budget_and_deduplication():
    client = TestClient(create_app(FakeService()))
    body = dict(collection="c", item="i", profile="rgb", tiles=[[1, 1, 8], [1, 1, 8]])
    assert client.post("/api/bdc/prewarm", json=body).json()["completed"] == 1
    body["tiles"] = [[1, 1, 8]] * 10
    assert client.post("/api/bdc/prewarm", json=body).status_code == 422
    body["tiles"] = [[-1, 1, 8]]
    assert client.post("/api/bdc/prewarm", json=body).status_code == 422


def test_invalid_area_and_dates_fail_before_search(tmp_path, monkeypatch):
    service = BdcService(tmp_path)
    monkeypatch.setattr(service, "collection", lambda _: {})
    for bbox, start, end in [
        ([-180, -80, 180, 80], "2024-01-01", "2024-01-02"),
        ([-50, -17, -49, -16], "2024-02-30", "2024-03-01"),
        ([-50, -17, -49, -16], "2024-03-01", "2024-01-01"),
    ]:
        with pytest.raises(ValueError):
            service.search("x", bbox, start, end)


def test_concurrent_tiles_are_rendered_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    service = BdcService(tmp_path)
    calls = []
    profile = {
        "id": "rgb",
        "bands": ["r", "g", "b"],
        "min": [0] * 3,
        "max": [255] * 3,
        "gamma": 1,
        "scale": 1,
        "offset": 0,
        "ndvi": False,
    }
    monkeypatch.setattr(
        service, "detail", lambda *args: {"item": {"id": "x"}, "profiles": [profile]}
    )

    def pixels(*args):
        calls.append(1)
        return np.full((256, 256, 4), 255, dtype="uint8")

    monkeypatch.setattr(service, "render_pixels", pixels)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: service.render("c", "i", 14, 1, 1, "rgb", "jpeg"), range(4)
            )
        )
    assert len(calls) == 1
    assert sorted(stats["cache"] for _, stats in results) == [
        "HIT",
        "HIT",
        "HIT",
        "MISS",
    ]
    assert len({data for data, _ in results}) == 1


def test_metadata_refresh_preserves_cached_snapshot_when_paused(tmp_path, monkeypatch):
    import json

    from app.modules.bdc.transport import Transport, digest

    class Response:
        status_code = 200
        headers = {}
        content = b'{"version": 2}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    url = "https://data.inpe.br/bdc/stac/v1/collections"
    key = digest(["GET", url, None])
    (tmp_path / (key + ".json")).write_text(
        json.dumps({"status": 200, "fetched_at": 0})
    )
    (tmp_path / (key + ".body")).write_bytes(b'{"version": 1}')
    (tmp_path / (key + ".attempted")).touch()
    transport = Transport(tmp_path, metadata_ttl=3600)
    calls = []
    monkeypatch.setattr(
        transport.session,
        "request",
        lambda *args, **kwargs: calls.append(1) or Response(),
    )
    (tmp_path / "data.inpe.br.paused").write_text("503")
    assert transport.json(url) == {"version": 1} and calls == []
    (tmp_path / "data.inpe.br.paused").unlink()
    assert transport.json(url) == {"version": 2} and calls == [1]
    assert transport.json(url) == {"version": 2} and calls == [1]
