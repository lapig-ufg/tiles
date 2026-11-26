from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from poc.bdc_cog.catalog import landsat_items, period_dates, search, sensor, tile_region
from poc.bdc_cog.compare import fit_radiometry, metrics
from poc.bdc_cog.render import visualize
from poc.bdc_cog.transport import Transport, ServicePaused, validate_url


@pytest.mark.parametrize(
    "year,expected",
    [
        (1985, "LT05"),
        (2011, "LT05"),
        (2012, "LE07"),
        (2013, "LE07"),
        (2014, "LC08"),
        (2024, "LC08"),
        (2025, "LC09"),
    ],
)
def test_sensor_boundaries(year, expected):
    assert sensor(year) == expected


@pytest.mark.parametrize(
    "period,year,month,end",
    [
        ("MONTH", 2024, 2, "2024-02-29"),
        ("MONTH", 2023, 2, "2023-02-28"),
        ("WET", 2024, 1, "2024-04-30"),
        ("DRY", 2024, 1, "2024-10-30"),
    ],
)
def test_exclusive_dates(period, year, month, end):
    assert period_dates(period, year, month)[1] == end


def test_invalid_dates_and_tiles():
    for args in [("MONTH", 2024, 13), ("OTHER", 2024, 1)]:
        with pytest.raises(ValueError):
            period_dates(*args)
    for xyz in [(0, 0, 5), (-1, 0, 8), (256, 0, 8), (0, 256, 8)]:
        with pytest.raises(ValueError):
            tile_region(*xyz)


def scene(cloud, sensor="LC08", tier="T1", product="L2SP", day="01"):
    return {
        "id": f"{sensor}_{product}_222071_202408{day}_20240901_02_{tier}",
        "properties": {"eo:cloud_cover": cloud},
    }


def test_tier_sensor_and_best_policy():
    items = [
        scene(1),
        scene(39, day="02"),
        scene(40, day="03"),
        scene(0, tier="T2"),
        scene(0, sensor="LC09"),
    ]
    assert landsat_items(items, 2024, "BEST_IMAGE", "legacy-desc") == [items[1]]
    assert landsat_items(items, 2024, "BEST_IMAGE", "least-cloud") == [items[0]]
    with pytest.raises(ValueError):
        landsat_items(items, 2024, "BEST_IMAGE", None)
    assert (
        len(landsat_items(items + [scene(5, product="L2SR")], 2024, "MOSAIC", None))
        == 4
    )


def test_rgb_scaling_gamma_clipping_and_alpha():
    data = np.array([[[-1, 0.25, 2]]] * 3)
    pixels = visualize(data, np.array([[True, True, False]]), [0] * 3, [1] * 3, 2)
    assert pixels.tolist() == [[[0, 0, 0, 255], [128, 128, 128, 255], [0, 0, 0, 0]]]


def test_ndvi_negative_zero_and_palette_endpoints():
    data = np.array([[[-1, 0, 0, 1]], [[1, 0, 1, 0]]], float)
    pixels = visualize(data, np.ones((1, 4), bool), [-1], [1], 1, True)
    assert pixels[0, :, 3].tolist() == [0, 0, 255, 255]
    assert pixels[0, 2, :3].tolist() == [165, 42, 42]
    assert pixels[0, 3, :3].tolist() == [0, 104, 55]


def test_metrics_exclude_alpha_and_report_coverage():
    a = np.zeros((256, 256, 4), dtype="uint8")
    a[..., 3] = 255
    b = a.copy()
    b[0, 0, 3] = 0
    b[1:, 1:, :3] = 20
    result = metrics(a, b)
    assert result["coverage_disagreement_pct"] == 100 / 65536
    assert result["valid_pixels"] == 65535
    assert 19 < result["mae"][0] < 20
    assert metrics(a, a)["ssim"] == [1, 1, 1]
    assert metrics(a, np.zeros_like(a))["mae"] is None


def test_calibration_with_radiometric_pairs():
    x = np.arange(100)
    y = 2 * x + 10
    fit = fit_radiometry(x, y, np.ones(100, bool), 30, 110)
    assert fit["a"] == pytest.approx(2)
    assert fit["min"] == pytest.approx(10)
    assert fit["max"] == pytest.approx(50)
    with pytest.raises(ValueError):
        fit_radiometry(x, -y, np.ones(100, bool), 30, 110)


@pytest.mark.parametrize(
    "url",
    [
        "http://data.inpe.br/a",
        "https://evil.invalid/a",
        "https://data.inpe.br@evil.invalid/a",
        "https://a:data@data.inpe.br/a",
        "https://data.inpe.br:444/a",
        "file:///tmp/a",
    ],
)
def test_remote_allowlist(url):
    with pytest.raises(ValueError):
        validate_url(url)


class Response:
    def __init__(self, status=200, body=b"data"):
        self.status_code = status
        self.headers = {}
        self.content = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_transport_deduplicates_concurrent_requests(tmp_path, monkeypatch):
    transport = Transport(tmp_path)
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return Response()

    monkeypatch.setattr(transport.session, "request", request)
    with ThreadPoolExecutor(4) as pool:
        result = list(
            pool.map(lambda _: transport.request("https://data.inpe.br/a")[1], range(8))
        )
    assert result == [b"data"] * 8
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_service_pause_is_persistent(tmp_path, monkeypatch, status):
    t = Transport(tmp_path)
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return Response(status)

    monkeypatch.setattr(t.session, "request", request)
    for path in ["a", "b"]:
        with pytest.raises(ServicePaused):
            t.request("https://data.inpe.br/" + path)
    assert len(calls) == 1
    with pytest.raises(ServicePaused):
        Transport(tmp_path).request("https://data.inpe.br/c")


def test_range_ignored_does_not_download(tmp_path, monkeypatch):
    t = Transport(tmp_path)
    monkeypatch.setattr(t.session, "request", lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match="ignorou Range"):
        t.request("https://data.inpe.br/a", byte_range="bytes=0-10")


def test_failed_response_is_not_repeated(tmp_path, monkeypatch):
    t = Transport(tmp_path)
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return Response(404)

    monkeypatch.setattr(t.session, "request", request)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            t.request("https://data.inpe.br/a")
    assert len(calls) == 1


def test_pagination_and_exclusive_end():
    class Client:
        def __init__(self):
            self.count = 0

        def json(self, url):
            self.count += 1
            return {
                "features": [
                    {
                        "id": str(self.count),
                        "properties": {
                            "datetime": "2024-08-01"
                            if self.count == 1
                            else "2024-08-31"
                        },
                    }
                ],
                "links": [{"rel": "next"}] if self.count == 1 else [],
            }

    t = Client()
    assert [
        i["id"]
        for i in search(
            t, "landsat-2", [-50, -17, -49, -16], "2024-08-01", "2024-08-31"
        )
    ] == ["1"]
    assert t.count == 2


def test_inversion_quantization_interval_contains_known_radiometry():
    from poc.bdc_cog.calibrate import inverse_png

    data = np.full((3, 8, 8), 0.25)
    rgba = visualize(data, np.ones((8, 8), bool), [0] * 3, [1] * 3, 1.2)
    estimate, interval = inverse_png(
        np.moveaxis(rgba[..., :3], -1, 0), [0] * 3, [1] * 3, 1.2
    )
    assert np.all(np.abs(estimate - data) <= interval)


def test_intersection_preserves_off_tile_best_without_replacement():
    from poc.bdc_cog.render import intersects_tile

    assert not intersects_tile({"bbox": [10, 10, 11, 11]}, 5943, 8963, 14)
    assert intersects_tile({"bbox": [-50, -17, -49, -16]}, 5943, 8963, 14)


@pytest.mark.parametrize(
    "minimum,maximum,gamma",
    [
        ([0], [1], 0),
        ([0], [1], float("nan")),
        ([float("nan")], [1], 1),
        ([0], [float("inf")], 1),
        ([1], [0], 1),
    ],
)
def test_invalid_visual_parameters(minimum, maximum, gamma):
    with pytest.raises(ValueError):
        visualize(np.zeros((3, 2, 2)), np.ones((2, 2), bool), minimum, maximum, gamma)


def test_range_integrity(tmp_path, monkeypatch):
    t = Transport(tmp_path)
    response = Response(206, b"abcd")
    response.headers = {"Content-Range": "bytes 0-3/100"}
    monkeypatch.setattr(t.session, "request", lambda *a, **k: response)
    assert t.request("https://data.inpe.br/a", byte_range="bytes=0-3")[1] == b"abcd"
    response.headers = {"Content-Range": "bytes 1-4/100"}
    with pytest.raises(RuntimeError, match="inconsistente"):
        t.request("https://data.inpe.br/b", byte_range="bytes=0-3")


def test_redirect_not_followed(tmp_path, monkeypatch):
    t = Transport(tmp_path)

    def request(*args, **kwargs):
        assert kwargs["allow_redirects"] is False
        return Response(302)

    monkeypatch.setattr(t.session, "request", request)
    with pytest.raises(RuntimeError, match="Redirecionamento"):
        t.request("https://data.inpe.br/a")


def test_incomplete_attempt_is_not_repeated(tmp_path, monkeypatch):
    import requests

    t = Transport(tmp_path)
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        raise requests.Timeout("timeout")

    monkeypatch.setattr(t.session, "request", request)
    with pytest.raises(requests.Timeout):
        t.request("https://tiles.lapig.iesa.ufg.br/a")
    with pytest.raises(RuntimeError, match="Tentativa anterior"):
        t.request("https://tiles.lapig.iesa.ufg.br/a")
    assert len(calls) == 1
