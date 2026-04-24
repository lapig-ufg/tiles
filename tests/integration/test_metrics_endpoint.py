"""Integração mínima: endpoint /metrics responde + middleware observa requests
de tile via path + status + header X-Error-Reason.
"""
from __future__ import annotations

import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


@pytest.fixture
def app_with_metrics_middleware():
    from tests.conftest import reset_app_imports
    reset_app_imports()

    from app.core.metrics import (
        tile_requests_total,
        tile_duration_seconds,
        cache_hits_total,
        observe_request,
    )
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    # zera contadores pra isolamento entre testes
    for c in (tile_requests_total, tile_duration_seconds, cache_hits_total):
        c.clear()

    app = FastAPI()

    class _MetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            start = time.monotonic()
            response = await call_next(request)
            observe_request(
                path=request.url.path,
                status_code=response.status_code,
                error_reason=response.headers.get("X-Error-Reason"),
                duration_seconds=time.monotonic() - start,
            )
            return response

    app.add_middleware(_MetricsMiddleware)

    @app.get("/api/layers/landsat/{x}/{y}/{z}")
    def _ok(x: int, y: int, z: int):
        return Response(content=b"png", media_type="image/png")

    @app.get("/api/layers/landsat/err/{x}/{y}/{z}")
    def _err(x: int, y: int, z: int):
        return Response(
            content=b"png",
            media_type="image/png",
            status_code=503,
            headers={"X-Error-Reason": "ee_unavailable"},
        )

    @app.get("/metrics")
    def metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def test_metrics_endpoint_exposes_prometheus_content_type(app_with_metrics_middleware):
    client = TestClient(app_with_metrics_middleware)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


def test_successful_tile_increments_counter(app_with_metrics_middleware):
    client = TestClient(app_with_metrics_middleware)
    client.get("/api/layers/landsat/1/2/3")

    metrics_resp = client.get("/metrics")
    body = metrics_resp.text
    assert 'tile_requests_total{error_reason="ok",layer="landsat",status_class="2xx"} 1.0' in body


def test_error_tile_counter_uses_x_error_reason(app_with_metrics_middleware):
    client = TestClient(app_with_metrics_middleware)
    client.get("/api/layers/landsat/err/1/2/3")

    body = client.get("/metrics").text
    assert 'tile_requests_total{error_reason="ee_unavailable",layer="landsat",status_class="5xx"} 1.0' in body


def test_metrics_endpoint_itself_is_not_counted(app_with_metrics_middleware):
    client = TestClient(app_with_metrics_middleware)
    client.get("/metrics")
    client.get("/metrics")

    body = client.get("/metrics").text
    # Nenhuma entrada com layer="other" deve aparecer — /metrics é classificado
    # como "other" e observe_request o ignora.
    assert 'layer="other"' not in body
