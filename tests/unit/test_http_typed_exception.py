"""http_get_bytes deve lançar EarthEngineRateLimitedError ao esgotar retries
em 429 — permite que o caller diferencie 429 (recuperável via rotação) de
falhas HTTP genéricas (irrecuperáveis)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import http as http_mod
from app.utils.http import EarthEngineRateLimitedError, http_get_bytes


def _fake_aiohttp_session(status: int):
    """Constrói um ClientSession fake cujo .get(...) devolve um response
    com o status pedido. Suporta o padrão `async with session.get(...) as r`."""
    resp = MagicMock()
    resp.status = status
    resp.read = AsyncMock(return_value=b"")
    resp.reason = "Too Many Requests"
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)

    sess = MagicMock()
    sess.get = MagicMock(return_value=resp)
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=None)
    return sess


@pytest.mark.asyncio
async def test_exception_class_exists_and_carries_sa_name():
    """EarthEngineRateLimitedError é exportada e aceita sa_name opcional."""
    exc = EarthEngineRateLimitedError("rate limited", sa_name="sa-x@proj")
    assert isinstance(exc, Exception)
    assert exc.sa_name == "sa-x@proj"


@pytest.mark.asyncio
async def test_persistent_429_raises_typed_exception():
    """Após esgotar todas as tentativas com 429, deve subir
    EarthEngineRateLimitedError — não HTTPException."""
    fake = _fake_aiohttp_session(status=429)

    with patch("app.utils.http.aiohttp.ClientSession", return_value=fake):
        with pytest.raises(EarthEngineRateLimitedError):
            await http_get_bytes(
                "http://example.com/tile.png",
                max_retries=2,
                base_delay=0.0,
            )
