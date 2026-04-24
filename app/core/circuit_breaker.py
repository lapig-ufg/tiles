"""Circuit breaker simples para proteger chamadas ao Earth Engine (PR #5).

Estado mantido no Redis compartilhado entre réplicas — quando uma réplica
abre o circuito, as outras enxergam e cortam tráfego imediatamente para o
EE, liberando threads presas e impedindo retry storm.

Não pretende ser um breaker completo (half-open, bulkhead, etc.). O objetivo
é fail-fast sob falha sistêmica do EE, trocando retry exponencial por 503
imediato com `Retry-After`.

Uso:
    breaker = CircuitBreaker(redis_client)
    if await breaker.is_open():
        retry_after = await breaker.seconds_until_retry()
        return tile_error_response(status_code=503, reason="ee_unavailable",
                                    retry_after=retry_after)
    try:
        result = await ee_call(...)
        await breaker.record_success()
    except EEException:
        await breaker.record_failure()
        raise
"""
from __future__ import annotations

import time
from typing import Protocol


def _now() -> int:
    """Epoch segundos — dedicado em módulo para mock fácil em testes."""
    return int(time.time())


class _AsyncRedis(Protocol):
    async def incr(self, key: str) -> int: ...
    async def expire(self, key: str, seconds: int) -> object: ...
    async def get(self, key: str) -> object: ...
    async def set(self, key: str, value, *, ex: int | None = None) -> object: ...


class CircuitBreaker:
    def __init__(
        self,
        redis_client: _AsyncRedis,
        *,
        threshold: int = 20,
        window_seconds: int = 10,
        cooldown_seconds: int = 30,
        key_prefix: str = "cb:ee",
    ) -> None:
        """Parâmetros:

        - threshold: número de falhas na janela que dispara abertura.
        - window_seconds: duração da janela deslizante de contagem.
        - cooldown_seconds: tempo que o circuito fica aberto após tripping.
        - key_prefix: prefixo das chaves no Redis (permite múltiplos breakers).
        """
        self._redis = redis_client
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.key_prefix = key_prefix

    # --- chaves internas -----------------------------------------------------

    @property
    def _open_until_key(self) -> str:
        return f"{self.key_prefix}:open_until"

    def _window_key(self) -> str:
        bucket = _now() // self.window_seconds
        return f"{self.key_prefix}:failures:{bucket}"

    # --- API pública ---------------------------------------------------------

    async def is_open(self) -> bool:
        raw = await self._redis.get(self._open_until_key)
        if raw is None:
            return False
        try:
            open_until = int(raw if not isinstance(raw, (bytes, bytearray)) else raw.decode())
        except (TypeError, ValueError):
            return False
        return open_until > _now()

    async def seconds_until_retry(self) -> int:
        raw = await self._redis.get(self._open_until_key)
        if raw is None:
            return 0
        try:
            open_until = int(raw if not isinstance(raw, (bytes, bytearray)) else raw.decode())
        except (TypeError, ValueError):
            return 0
        remaining = open_until - _now()
        return max(0, remaining)

    async def record_failure(self) -> None:
        key = self._window_key()
        count = await self._redis.incr(key)
        # TTL = 2× janela garante que contadores não acumulam entre ciclos.
        await self._redis.expire(key, self.window_seconds * 2)
        if count >= self.threshold:
            await self._redis.set(
                self._open_until_key,
                _now() + self.cooldown_seconds,
                ex=self.cooldown_seconds,
            )

    async def record_success(self) -> None:
        """No-op intencional — contadores expiram sozinhos pela janela.

        Mantido na API para caller expressar intent ("fechar cedo" seria
        reset explícito; preferimos simplicidade sobre otimização)."""
        return None


# ----------------------------------------------------------------------------- #
# Singleton lazy                                                                #
# ----------------------------------------------------------------------------- #

_breaker: CircuitBreaker | None = None


def get_ee_circuit_breaker() -> CircuitBreaker:
    """Retorna breaker singleton do EE. Opt-in via setting `CIRCUIT_BREAKER_ENABLED`.

    Quando desabilitado, devolve um breaker "dummy" (threshold muito alto)
    — caller não precisa testar feature flag.
    """
    global _breaker
    if _breaker is not None:
        return _breaker

    from app.core.config import REDIS_URL, settings
    import redis.asyncio as redis_async

    enabled = bool(settings.get("CIRCUIT_BREAKER_ENABLED", False))
    redis_client = redis_async.from_url(REDIS_URL)

    threshold = int(settings.get("CIRCUIT_BREAKER_THRESHOLD", 20))
    window = int(settings.get("CIRCUIT_BREAKER_WINDOW_SECONDS", 10))
    cooldown = int(settings.get("CIRCUIT_BREAKER_COOLDOWN_SECONDS", 30))

    if not enabled:
        # Threshold inatingível → nunca abre. Feature flag off por default.
        threshold = 10**9

    _breaker = CircuitBreaker(
        redis_client,
        threshold=threshold,
        window_seconds=window,
        cooldown_seconds=cooldown,
    )
    return _breaker


def _reset_for_tests() -> None:
    """Reseta o singleton — uso exclusivo de fixtures de teste."""
    global _breaker
    _breaker = None
