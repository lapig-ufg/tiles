"""Circuit breaker em ``ServiceAccountPool.acquire()``.

Antes desse fix, quando todas as SAs estavam em cooldown, o ``acquire``
chamava ``time.sleep(60s)`` síncrono, bloqueando o worker uvicorn. O fix
fail-fast (PoolExhaustedError → 503 com Retry-After) preserva o worker
para atender outras requisições.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.core.gee_pool import (
    PoolExhaustedError,
    ServiceAccountInfo,
    ServiceAccountPool,
)


def _make_pool_with_two_sas() -> ServiceAccountPool:
    """Constrói um pool com 2 SAs e Redis client mockado.

    O ``__init__`` é evitado para não tocar disco/Redis. Em vez disso,
    preenchemos os atributos necessários para ``acquire()``.
    """
    pool = ServiceAccountPool.__new__(ServiceAccountPool)
    pool._sa_directory = "/tmp/fake"
    pool._redis = MagicMock()
    pool._accounts = {
        "sa-a@proj": ServiceAccountInfo(name="sa-a@proj", file_path="/tmp/sa-a.json"),
        "sa-b@proj": ServiceAccountInfo(name="sa-b@proj", file_path="/tmp/sa-b.json"),
    }
    return pool


def test_acquire_raises_pool_exhausted_when_all_in_long_cooldown():
    """Todas as SAs com cooldown > timeout → PoolExhaustedError sem sleep.

    Garante que o worker não bloqueia por 60s; cliente recebe 503 e pode
    fazer retry com Retry-After.
    """
    pool = _make_pool_with_two_sas()
    now = time.time()
    cooldown_until = now + 30.0  # 30s no futuro — muito maior que timeout default 2s

    pool._redis.zrange.return_value = [
        ("sa-a@proj", 0),
        ("sa-b@proj", 0),
    ]
    pool._redis.hget.return_value = str(cooldown_until)

    start = time.time()
    with pytest.raises(PoolExhaustedError) as exc_info:
        pool.acquire(worker_id="worker-test-1")
    elapsed = time.time() - start

    assert elapsed < 0.5, f"acquire bloqueou por {elapsed:.2f}s — circuit breaker falhou"
    assert exc_info.value.retry_after > 25.0
    assert exc_info.value.retry_after <= 31.0


def test_acquire_returns_sa_when_one_is_available():
    """SA disponível (sem cooldown) → acquire retorna sem bloqueio."""
    pool = _make_pool_with_two_sas()

    pool._redis.zrange.return_value = [
        ("sa-a@proj", 0),
        ("sa-b@proj", 0),
    ]
    # Nenhuma SA em cooldown.
    pool._redis.hget.return_value = ""
    pool._redis.zincrby.return_value = 1.0
    pool._redis.set.return_value = True
    pool._redis.hincrby.return_value = 1

    # Evita carregamento real do JSON da SA (que não existe).
    with patch.object(ServiceAccountInfo, "load_credentials", return_value=MagicMock()):
        sa = pool.acquire(worker_id="worker-test-2")

    assert sa.name in {"sa-a@proj", "sa-b@proj"}


def test_acquire_waits_for_short_cooldown():
    """Cooldown abaixo do timeout aceitável → acquire espera e retorna a SA.

    Comportamento preserva chamadas que estavam quase pegando uma SA logo
    após o cooldown — não devolve 503 prematuramente.
    """
    pool = _make_pool_with_two_sas()
    now = time.time()
    cooldown_until = now + 0.05  # 50ms — muito menor que timeout default de 2s

    pool._redis.zrange.return_value = [
        ("sa-a@proj", 0),
        ("sa-b@proj", 0),
    ]
    pool._redis.hget.return_value = str(cooldown_until)
    pool._redis.zincrby.return_value = 1.0
    pool._redis.set.return_value = True
    pool._redis.hincrby.return_value = 1

    with patch.object(ServiceAccountInfo, "load_credentials", return_value=MagicMock()):
        start = time.time()
        sa = pool.acquire(worker_id="worker-test-3")
        elapsed = time.time() - start

    assert elapsed >= 0.04, f"esperado bloqueio breve, viu {elapsed:.3f}s"
    assert elapsed < 1.0, f"bloqueio longo demais: {elapsed:.3f}s"
    assert sa.name in {"sa-a@proj", "sa-b@proj"}


def test_pool_exhausted_total_counter_increments():
    """Métrica gee_pool_exhausted_total incrementa em cada exaustão.

    Permite alerta de SLO quando o GEE upstream está saturado.
    """
    from prometheus_client import REGISTRY

    pool = _make_pool_with_two_sas()
    now = time.time()
    cooldown_until = now + 30.0

    pool._redis.zrange.return_value = [
        ("sa-a@proj", 0),
        ("sa-b@proj", 0),
    ]
    pool._redis.hget.return_value = str(cooldown_until)

    before = REGISTRY.get_sample_value("gee_pool_exhausted_total") or 0.0
    with pytest.raises(PoolExhaustedError):
        pool.acquire(worker_id="worker-test-4")
    after = REGISTRY.get_sample_value("gee_pool_exhausted_total") or 0.0

    assert after == before + 1.0


def test_pool_exhausted_error_has_retry_after_attribute():
    """PoolExhaustedError carrega retry_after em segundos para mapeamento HTTP."""
    err = PoolExhaustedError(retry_after=12.5)
    assert err.retry_after == 12.5
    assert "12.5" in str(err)
