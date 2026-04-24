"""Testes unitários para `tile_error_response` (PR #1).

O helper substitui o antipadrão de retornar PNG placeholder com HTTP 200, que
causava cache poisoning em browsers e no cache Redis+S3.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.errors import tile_error_response


class TestBasicContract:
    def test_returns_status_code_explicitly_provided(self):
        resp = tile_error_response(status_code=500, reason="internal_error")
        assert resp.status_code == 500

    def test_media_type_is_png(self):
        resp = tile_error_response(status_code=500, reason="internal_error")
        assert resp.media_type == "image/png"

    def test_body_is_nonempty_bytes(self):
        resp = tile_error_response(status_code=500, reason="internal_error")
        assert resp.body and isinstance(resp.body, (bytes, bytearray))

    def test_sets_cache_control_no_store(self):
        resp = tile_error_response(status_code=500, reason="internal_error")
        assert resp.headers["cache-control"] == "no-store, must-revalidate"

    def test_sets_x_error_reason(self):
        resp = tile_error_response(status_code=500, reason="ee_band_missing")
        assert resp.headers["x-error-reason"] == "ee_band_missing"


class TestRateLimitResponse:
    def test_429_includes_retry_after_default(self):
        resp = tile_error_response(status_code=429, reason="ee_rate_limit")
        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "30"

    def test_429_respects_custom_retry_after(self):
        resp = tile_error_response(status_code=429, reason="ee_rate_limit", retry_after=5)
        assert resp.headers["retry-after"] == "5"

    def test_non_429_has_no_retry_after(self):
        resp = tile_error_response(status_code=500, reason="internal_error")
        assert "retry-after" not in {k.lower() for k in resp.headers.keys()}

    def test_503_gets_retry_after_only_when_explicit(self):
        # Sem retry_after → não inclui (default do servidor)
        resp = tile_error_response(status_code=503, reason="ee_unavailable")
        assert "retry-after" not in {k.lower() for k in resp.headers.keys()}

        # Com retry_after → inclui (ex.: CB sabe quanto falta do cooldown)
        resp2 = tile_error_response(
            status_code=503, reason="ee_unavailable", retry_after=25,
        )
        assert resp2.headers["retry-after"] == "25"


class TestInferenceFromException:
    def test_from_http_exception_preserves_status(self):
        exc = HTTPException(status_code=503, detail="Earth Engine temporarily unavailable")
        resp = tile_error_response.from_exception(exc)
        assert resp.status_code == 503
        assert resp.headers["x-error-reason"] == "ee_unavailable"

    def test_from_http_exception_rate_limit_maps_to_429(self):
        exc = HTTPException(status_code=429, detail="too many requests")
        resp = tile_error_response.from_exception(exc)
        assert resp.status_code == 429
        assert resp.headers["x-error-reason"] == "ee_rate_limit"
        assert resp.headers["retry-after"] == "30"

    def test_from_ee_band_missing_maps_to_500(self):
        import ee
        exc = ee.EEException('Image has no band named "SR_B4".')
        resp = tile_error_response.from_exception(exc)
        assert resp.status_code == 500
        assert resp.headers["x-error-reason"] == "ee_band_missing"

    def test_from_generic_exception_maps_to_500_internal(self):
        exc = RuntimeError("wat")
        resp = tile_error_response.from_exception(exc)
        assert resp.status_code == 500
        assert resp.headers["x-error-reason"] == "internal_error"


class TestDoesNotLeakInternalMessage:
    """X-Error-Reason é código curto para máquina.
    Nunca vazar detalhe bruto da exceção (mensagem pode conter caminho, IP, etc.)."""

    def test_reason_header_is_short_code_not_full_message(self):
        exc = HTTPException(503, detail="Earth Engine down at internal.host:5432")
        resp = tile_error_response.from_exception(exc)
        assert "internal.host" not in resp.headers["x-error-reason"]
        assert resp.headers["x-error-reason"].islower()
        assert " " not in resp.headers["x-error-reason"]
