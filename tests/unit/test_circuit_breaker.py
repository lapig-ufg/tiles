"""Circuit breaker minimal para proteger chamadas ao Earth Engine (PR #5).

Janela deslizante em Redis — implementação testada com fakeredis assíncrono.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class FakeRedis:
    """Mock async-redis mínimo: só INCR, SET, GET, EXPIRE, DELETE."""
    def __init__(self):
        self.store: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        current = int(self.store.get(key, "0")) + 1
        self.store[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> None:
        # Não implementa TTL real — suficiente para lógica do circuit breaker.
        pass

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value, *, ex: int | None = None) -> None:
        self.store[key] = str(value)

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n


@pytest.fixture
def now_mock(monkeypatch):
    """Fixa o tempo para avaliação determinística. Retorna [t] mutável."""
    t = [1_000_000]
    def _now():
        return t[0]
    from app.core import circuit_breaker as cb
    monkeypatch.setattr(cb, "_now", _now)
    return t


@pytest.fixture
def breaker(now_mock):
    from app.core.circuit_breaker import CircuitBreaker
    return CircuitBreaker(
        FakeRedis(),
        threshold=3,
        window_seconds=10,
        cooldown_seconds=30,
        key_prefix="cb:test",
    )


@pytest.mark.asyncio
async def test_closed_by_default(breaker):
    assert await breaker.is_open() is False


@pytest.mark.asyncio
async def test_opens_after_threshold_failures(breaker):
    await breaker.record_failure()
    await breaker.record_failure()
    assert await breaker.is_open() is False, "2 falhas < threshold=3"

    await breaker.record_failure()
    assert await breaker.is_open() is True, "3ª falha atinge threshold"


@pytest.mark.asyncio
async def test_open_respects_cooldown(breaker, now_mock):
    for _ in range(3):
        await breaker.record_failure()
    assert await breaker.is_open() is True

    now_mock[0] += 15  # metade do cooldown
    assert await breaker.is_open() is True

    now_mock[0] += 20  # total 35s > 30s cooldown
    assert await breaker.is_open() is False


@pytest.mark.asyncio
async def test_failures_in_separate_windows_dont_accumulate(breaker, now_mock):
    """Janela deslizante: falhas de 15s atrás não devem contar."""
    await breaker.record_failure()
    await breaker.record_failure()

    now_mock[0] += 11  # passou a janela de 10s
    await breaker.record_failure()

    # Nesta nova janela só há 1 falha — threshold (3) não atingido.
    assert await breaker.is_open() is False


@pytest.mark.asyncio
async def test_seconds_until_retry_when_open(breaker, now_mock):
    for _ in range(3):
        await breaker.record_failure()

    assert await breaker.seconds_until_retry() == 30

    now_mock[0] += 12
    assert await breaker.seconds_until_retry() == 18

    now_mock[0] += 100  # cooldown expirou
    assert await breaker.seconds_until_retry() == 0


@pytest.mark.asyncio
async def test_record_success_does_not_crash(breaker):
    """`record_success` é no-op mas parte da API pública."""
    for _ in range(2):
        await breaker.record_failure()
    await breaker.record_success()
    assert await breaker.is_open() is False
