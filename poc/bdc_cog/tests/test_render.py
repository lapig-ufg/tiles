import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from morecantile import tms

from poc.bdc_cog import render
from poc.bdc_cog.transport import Transport


class LocalBridge:
    def __init__(self, *args):
        self.errors = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def url(self, path):
        return path


def item(tmp_path, name, bands):
    assets = {}
    box = tms.get("WebMercatorQuad").xy_bounds(5943, 8963, 14)
    for band, value in bands.items():
        path = tmp_path / f"{name}-{band}.tif"
        pixels = (
            np.full((256, 256), value, dtype="uint16") if np.isscalar(value) else value
        )
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=256,
            height=256,
            count=1,
            dtype="uint16",
            crs="EPSG:3857",
            transform=from_bounds(*box, 256, 256),
            nodata=0,
        ) as target:
            target.write(pixels, 1)
        assets[band] = {"href": str(path)}
    return dict(id=name, assets=assets)


def test_real_tiff_scale_nodata_mosaic_and_qa(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "RangeBridge", LocalBridge)
    t = Transport(tmp_path / "http")
    first = item(
        tmp_path, "first", dict(red=10000, green=10000, blue=10000, qa_pixel=1)
    )
    qa = np.ones((256, 256), dtype="uint16")
    qa[:128] = 8
    qa[128:192] = 16
    second = item(
        tmp_path, "second", dict(red=15000, green=15000, blue=15000, qa_pixel=qa)
    )
    a = render.render_items(t, [first], "landsat-2", "landsat-tvi-true", 5943, 8963, 14)
    b = render.render_items(
        t, [second], "landsat-2", "landsat-tvi-true", 5943, 8963, 14
    )
    composed = render.render_items(
        t, [first, second], "landsat-2", "landsat-tvi-true", 5943, 8963, 14, "MOSAIC"
    )
    np.testing.assert_array_equal(composed[:192], a[:192])
    np.testing.assert_array_equal(composed[192:], b[192:])
    expected = render.visualize(
        np.full((3, 256, 256), 0.075),
        np.ones((256, 256), bool),
        [0.03, 0.03, 0],
        [0.25] * 3,
        1.2,
    )
    np.testing.assert_array_equal(a, expected)


def test_cache_invalidates_for_visual_content_and_scene_order(tmp_path, monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return np.zeros((256, 256, 4), dtype="uint8")

    monkeypatch.setattr(render, "render_items", fake)
    t = Transport(tmp_path / "http")
    args = (
        t,
        tmp_path / "png",
        [{"id": "a"}, {"id": "b"}],
        "landsat-2",
        "landsat-tvi-true",
        5943,
        8963,
        14,
    )
    _, one = render.render_cached(*args)
    _, two = render.render_cached(*args)
    assert not one["png_hit"] and two["png_hit"]
    modified = list(args)
    modified[2] = list(reversed(modified[2]))
    render.render_cached(*modified)
    assert len(calls) == 2
    monkeypatch.setitem(
        render.VIS, "landsat-tvi-true", (["red", "green", "blue"], [0] * 3, [1] * 3, 1)
    )
    render.render_cached(*args)
    assert len(calls) == 3


def test_missing_band_fails_without_png(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "RangeBridge", LocalBridge)
    t = Transport(tmp_path / "http")
    entry = item(tmp_path, "missing", dict(red=10000))
    with pytest.raises(KeyError):
        render.render_cached(
            t,
            tmp_path / "png",
            [entry],
            "landsat-2",
            "landsat-tvi-true",
            5943,
            8963,
            14,
        )
    assert not list((tmp_path / "png").glob("*.png"))


def test_sentinel_requires_offset_and_declared_nodata(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "RangeBridge", LocalBridge)
    t = Transport(tmp_path / "http")
    entry = item(tmp_path, "s2", dict(B04=0, B03=1000, B02=1000))
    with pytest.raises(ValueError, match="offset"):
        render.render_items(t, [entry], "S2_L2A-1", "tvi-rgb", 5943, 8963, 14)
    a = render.render_items(
        t, [entry], "S2_L2A-1", "tvi-rgb", 5943, 8963, 14, s2_offset=-1000
    )
    assert not a[..., 3].any()


@pytest.mark.parametrize("asset_key", ["rgb", "RGB", "tci"])
def test_cbers_asset_variants(tmp_path, monkeypatch, asset_key):
    monkeypatch.setattr(render, "RangeBridge", LocalBridge)
    box = tms.get("WebMercatorQuad").xy_bounds(5943, 8963, 14)
    path = tmp_path / "rgb.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=256,
        height=256,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_bounds(*box, 256, 256),
    ) as target:
        target.write(np.full((3, 256, 256), 128, dtype="uint8"))
    entry = {"id": "cbers", "assets": {asset_key: {"href": str(path)}}}
    result = render.render_items(
        Transport(tmp_path / "http"),
        [entry],
        "CB4A-WPM-PCA-FUSED-1",
        "bdc-cbers-rgb-proposta",
        5943,
        8963,
        14,
    )
    assert (result[..., :3] == 128).all()
    assert (result[..., 3] == 255).all()


def test_ambiguous_rgb_asset_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "RangeBridge", LocalBridge)
    entry = {"id": "cbers", "assets": {"rgb": {}, "tci": {}}}
    with pytest.raises(ValueError, match="ambíguo"):
        render.render_items(
            Transport(tmp_path / "http"),
            [entry],
            "CB4A-WPM-PCA-FUSED-1",
            "bdc-cbers-rgb-proposta",
            5943,
            8963,
            14,
        )
