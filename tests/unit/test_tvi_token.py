"""Cliente de tokens GEE efêmeros do tvi-api (spec tvi-api 2026-08-12 §8)."""
import time

import pytest

from app.core import tvi_token
from app.core.tvi_token import TviTokenDenied, get_campaign_token


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _ok(expires_in_ms):
    return FakeResponse(200, {
        "accessToken": "at-1",
        "projectId": "proj-1",
        "expiresAt": int(time.time() * 1000) + expires_in_ms,
        "mode": "OWNER",
    })


def test_token_cacheado_ate_5min_do_vencimento(monkeypatch):
    tvi_token.clear_cache()
    calls = []
    monkeypatch.setattr(tvi_token, "_post", lambda cid: calls.append(cid) or _ok(3_600_000))
    first = get_campaign_token("c1")
    second = get_campaign_token("c1")
    assert first["access_token"] == "at-1" and second is not None
    assert calls == ["c1"]  # segunda chamada veio do cache


def test_token_perto_de_expirar_renova(monkeypatch):
    tvi_token.clear_cache()
    calls = []
    monkeypatch.setattr(tvi_token, "_post", lambda cid: calls.append(cid) or _ok(60_000))
    get_campaign_token("c1")
    get_campaign_token("c1")
    assert calls == ["c1", "c1"]  # 60 s restantes < 5 min -> renova


def test_409_vira_excecao_nao_retentavel(monkeypatch):
    tvi_token.clear_cache()
    monkeypatch.setattr(
        tvi_token, "_post",
        lambda cid: FakeResponse(409, {"code": "gee.campaign_unlinked"}),
    )
    with pytest.raises(TviTokenDenied):
        get_campaign_token("c1")


def test_erro_5xx_sobe_como_excecao_retentavel(monkeypatch):
    tvi_token.clear_cache()
    monkeypatch.setattr(tvi_token, "_post", lambda cid: FakeResponse(500, {}))
    with pytest.raises(RuntimeError):
        get_campaign_token("c1")
