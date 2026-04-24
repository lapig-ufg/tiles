"""Métricas Prometheus para o endpoint de tile (PR #6).

Testes unitários da infraestrutura de métrica; integração com handlers em
`tests/integration/test_metrics_middleware.py`.
"""
from __future__ import annotations

import pytest
from prometheus_client import REGISTRY


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Zera contadores entre testes para isolamento."""
    from app.core import metrics as m
    for col in (m.tile_requests_total, m.tile_duration_seconds, m.cache_hits_total):
        col.clear()
    yield


class TestRegistry:
    def test_tile_requests_total_is_counter_with_expected_labels(self):
        from app.core.metrics import tile_requests_total
        labels = set(tile_requests_total._labelnames)
        assert labels == {"layer", "status_class", "error_reason"}

    def test_tile_duration_seconds_is_histogram_with_layer_label(self):
        from app.core.metrics import tile_duration_seconds
        assert "layer" in tile_duration_seconds._labelnames

    def test_cache_hits_total_has_layer_and_type_labels(self):
        from app.core.metrics import cache_hits_total
        labels = set(cache_hits_total._labelnames)
        assert labels == {"layer", "type"}


class TestClassifyStatus:
    def test_2xx_class(self):
        from app.core.metrics import status_class
        assert status_class(200) == "2xx"
        assert status_class(204) == "2xx"

    def test_4xx_class(self):
        from app.core.metrics import status_class
        assert status_class(404) == "4xx"
        assert status_class(429) == "4xx"

    def test_5xx_class(self):
        from app.core.metrics import status_class
        assert status_class(500) == "5xx"
        assert status_class(503) == "5xx"


class TestClassifyLayer:
    def test_landsat_path(self):
        from app.core.metrics import layer_from_path
        assert layer_from_path("/api/layers/landsat/100/100/10") == "landsat"
        assert layer_from_path("/landsat/100/100/10") == "landsat"

    def test_s2_path(self):
        from app.core.metrics import layer_from_path
        assert layer_from_path("/api/layers/s2_harmonized/100/100/10") == "s2_harmonized"

    def test_imagery_path(self):
        from app.core.metrics import layer_from_path
        assert layer_from_path("/api/imagery/landsat/L5/100/100/10") == "imagery"

    def test_other_paths_classified_as_other(self):
        from app.core.metrics import layer_from_path
        assert layer_from_path("/health") == "other"
        assert layer_from_path("/metrics") == "other"


class TestObserveRequest:
    def test_counter_increments_on_observation(self):
        from app.core.metrics import tile_requests_total, observe_request

        observe_request(
            path="/api/layers/landsat/1/2/3",
            status_code=200,
            error_reason=None,
            duration_seconds=0.15,
        )
        value = tile_requests_total.labels(
            layer="landsat", status_class="2xx", error_reason="ok"
        )._value.get()
        assert value == 1.0

    def test_error_reason_is_used_when_header_present(self):
        from app.core.metrics import tile_requests_total, observe_request

        observe_request(
            path="/api/layers/landsat/1/2/3",
            status_code=503,
            error_reason="ee_unavailable",
            duration_seconds=0.05,
        )
        value = tile_requests_total.labels(
            layer="landsat", status_class="5xx", error_reason="ee_unavailable"
        )._value.get()
        assert value == 1.0

    def test_non_tile_paths_are_not_counted(self):
        from app.core.metrics import tile_requests_total, observe_request

        observe_request("/health", 200, None, 0.01)
        observe_request("/metrics", 200, None, 0.01)

        # Nenhum sample com layer=other — não contamos rotas administrativas.
        samples = [s for m in tile_requests_total.collect() for s in m.samples]
        for s in samples:
            assert s.labels.get("layer") != "other"

    def test_duration_histogram_records(self):
        from app.core.metrics import tile_duration_seconds, observe_request

        observe_request("/api/layers/landsat/1/2/3", 200, None, 0.15)
        count = tile_duration_seconds.labels(layer="landsat")._sum.get()
        assert count == pytest.approx(0.15)
