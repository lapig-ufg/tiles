"""Integração: middleware injeta/responde X-Request-ID e a contextvar fica
disponível no corpo do handler."""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient


_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@pytest.fixture
def app_with_request_id():
    from app.middleware.request_id import RequestIdMiddleware, request_id_var

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo-rid")
    def _echo():
        return PlainTextResponse(request_id_var.get())

    return app


def test_generates_uuid4_when_header_absent(app_with_request_id):
    client = TestClient(app_with_request_id)
    resp = client.get("/echo-rid")

    assert resp.status_code == 200
    rid = resp.headers["x-request-id"]
    assert _UUID4_RE.match(rid), f"esperado UUID v4, veio {rid!r}"
    # O body vem da contextvar — deve bater com header.
    assert resp.text == rid


def test_preserves_incoming_header(app_with_request_id):
    client = TestClient(app_with_request_id)
    incoming = "trace-abc-123"
    resp = client.get("/echo-rid", headers={"X-Request-ID": incoming})

    assert resp.headers["x-request-id"] == incoming
    assert resp.text == incoming


def test_contextvar_cleans_up_after_request(app_with_request_id):
    from app.middleware.request_id import request_id_var

    client = TestClient(app_with_request_id)
    client.get("/echo-rid", headers={"X-Request-ID": "should-not-persist"})

    # Fora do lifecycle de uma request, volta ao default.
    assert request_id_var.get() == "-"
