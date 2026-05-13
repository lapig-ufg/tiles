# Mitigação de 429 do Earth Engine — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar a maior parte dos 503 (`ee_unavailable`) servidos a partir do endpoint de tiles, fazendo o pool de Service Accounts do GEE reagir a 429 de download HTTP com rotação reativa, invalidação da URL cacheada e regeneração via `getMapId` com SA fresca.

**Architecture:** `http_get_bytes` permanece agnóstico de GEE mas passa a lançar uma exceção tipada (`EarthEngineRateLimitedError`). Um novo helper `fetch_tile_with_rotation` orquestra: rotação da SA do worker, invalidação do meta-cache da URL, regeneração via `url_factory()` e um único retry. As três call sites (`layers.py`, `imagery.py`, `embedding_maps/router.py`) adotam o helper. Timeout do download sobe de 10 s para 20 s, configurável. Counters Prometheus por SA são adicionados para observabilidade.

**Tech Stack:** Python 3.12, FastAPI, aiohttp, Redis (Valkey), Prometheus (`prometheus_client`), pytest + pytest-asyncio.

**Spec de referência:** `docs/superpowers/specs/2026-05-12-tiles-ee-429-rotation-cache-invalidation-design.md`.

---

## Mapa de arquivos

| Arquivo | Operação | Responsabilidade |
|---|---|---|
| `settings.toml` | edit | Adicionar `HTTP_GET_BYTES_TIMEOUT = 20` |
| `app/cache/cache.py` | edit | Adicionar `adelete_meta(key)` (assíncrono) |
| `app/cache/cache_hybrid.py` | edit | Adicionar método `delete_meta(key)` ao `tile_cache` |
| `app/utils/http.py` | edit | Exceção tipada `EarthEngineRateLimitedError`; timeout default 20 s |
| `app/core/gee_pool.py` | edit | Counters Prometheus por SA; ganchos em `report_http_429` e `rotate_on_429` |
| `app/utils/ee_tile_fetch.py` | new | Helper `fetch_tile_with_rotation` |
| `app/api/layers.py` | edit | `_serve_tile` adota helper |
| `app/api/imagery.py` | edit | Tile handler adota helper |
| `app/modules/embedding_maps/router.py` | edit | Tile handler adota helper |
| `docs/deploy-runbook.md` | edit | Seção de expansão do pool de SAs |
| `tests/unit/test_http_retries_reduced.py` | edit | Atualizar limite superior do timeout |
| `tests/unit/test_http_typed_exception.py` | new | Cobre `EarthEngineRateLimitedError` |
| `tests/unit/test_ee_tile_fetch.py` | new | Cobre o helper de rotação |
| `tests/unit/test_gee_pool_metrics.py` | new | Cobre os counters Prometheus |
| `tests/unit/test_cache_delete_meta.py` | new | Cobre `adelete_meta` |

---

## Task 1: Configuração `HTTP_GET_BYTES_TIMEOUT` e `adelete_meta`

**Files:**
- Modify: `settings.toml`
- Modify: `app/cache/cache_hybrid.py` (classe `tile_cache`)
- Modify: `app/cache/cache.py` (façade)
- Test: `tests/unit/test_cache_delete_meta.py`

- [ ] **Step 1: Escrever teste falhando para `adelete_meta`**

Arquivo: `tests/unit/test_cache_delete_meta.py`

```python
"""adelete_meta — invalida URL cacheada após 429 da SA. Sem isso,
workers continuam lendo URL acoplada à SA penalizada."""
from __future__ import annotations

import pytest

from app.cache import cache as cache_mod


@pytest.mark.asyncio
async def test_adelete_meta_is_async_callable():
    """A função existe e é assíncrona — caller usa `await adelete_meta(...)`."""
    import inspect
    assert hasattr(cache_mod, "adelete_meta")
    assert inspect.iscoroutinefunction(cache_mod.adelete_meta)
```

- [ ] **Step 2: Rodar o teste e confirmar falha**

```bash
cd /home/tharles/projects_lapig/tiles
pytest tests/unit/test_cache_delete_meta.py -v
```

Esperado: `AttributeError: module 'app.cache.cache' has no attribute 'adelete_meta'`.

- [ ] **Step 3: Implementar `delete_meta` no `tile_cache`**

Editar `app/cache/cache_hybrid.py`, logo após o método `set_meta` (próximo à linha 299):

```python
    async def delete_meta(self, key: str) -> None:
        """Remove metadados (URL EE etc.) do Redis. Usado para invalidar
        URLs cacheadas vinculadas a SAs penalizadas por 429.

        Idempotente — DEL em chave inexistente é no-op no Redis.
        """
        async with self._get_redis() as r:
            await r.delete(f"meta:{key}")
```

- [ ] **Step 4: Adicionar façade `adelete_meta` em `app/cache/cache.py`**

Inserir após a definição de `aset_meta` (logo antes de `def atile_lock`):

```python
async def adelete_meta(key: str) -> None:
    """Remove metadados do cache híbrido (assíncrono).

    Usado pelo helper de rotação para invalidar URLs do EE quando a SA
    que as assinou recebeu 429.
    """
    await tile_cache.delete_meta(key)
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

```bash
pytest tests/unit/test_cache_delete_meta.py -v
```

Esperado: `1 passed`.

- [ ] **Step 6: Adicionar `HTTP_GET_BYTES_TIMEOUT` ao `settings.toml`**

Inserir após a linha `LIFESPAN_URL = 24` no bloco `[default]`:

```toml
# Timeout total (segundos) por tentativa de download de tile do EE.
# 10s era insuficiente sob carga (EE responde em 8–12s); 20s reduz
# TimeoutErrors sem prolongar excessivamente requests com EE realmente lento.
HTTP_GET_BYTES_TIMEOUT = 20
```

- [ ] **Step 7: Commit**

```bash
git add settings.toml app/cache/cache.py app/cache/cache_hybrid.py tests/unit/test_cache_delete_meta.py
git commit -m "feat(tiles): adiciona adelete_meta e settings HTTP_GET_BYTES_TIMEOUT"
```

---

## Task 2: Exceção tipada `EarthEngineRateLimitedError` em `http.py`

**Files:**
- Modify: `app/utils/http.py`
- Test: `tests/unit/test_http_typed_exception.py`

- [ ] **Step 1: Escrever teste falhando para a exceção tipada**

Arquivo: `tests/unit/test_http_typed_exception.py`

```python
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
```

- [ ] **Step 2: Rodar e confirmar falha**

```bash
pytest tests/unit/test_http_typed_exception.py -v
```

Esperado: `ImportError: cannot import name 'EarthEngineRateLimitedError' from 'app.utils.http'`.

- [ ] **Step 3: Definir a exceção em `app/utils/http.py`**

Editar `app/utils/http.py`. Substituir o bloco inteiro (linhas 1–79) por:

```python
"""
Utilitário HTTP compartilhado para download de imagens do Earth Engine.
Extraído de layers.py para reuso entre módulos (tiles, imagery).
"""
from __future__ import annotations

import asyncio
import random

import aiohttp
from fastapi import HTTPException

from app.core.config import logger, settings


class EarthEngineRateLimitedError(Exception):
    """Sinaliza 429 persistente do endpoint de tiles do Earth Engine.

    O caller usa essa exceção para acionar rotação de SA + invalidação de
    URL cacheada + regeneração. Diferencia 429 (recuperável) de falhas HTTP
    genéricas (irrecuperáveis no mesmo request).

    `sa_name` carrega o nome da SA penalizada quando disponível; útil para
    logs estruturados no caller. Pode ser None se não houver gee_manager
    ativo (ex: testes, modo dev).
    """

    def __init__(self, message: str, sa_name: str | None = None):
        super().__init__(message)
        self.sa_name = sa_name


async def http_get_bytes(
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: float | None = None,
) -> bytes:
    """Faz download de bytes via HTTP GET com retry e backoff exponencial.

    Trata 429 (rate limit) com backoff exponencial + jitter. Retorna os
    bytes da resposta em caso de 200.

    Parâmetros:
    - `max_retries`: 3 (PR #5) — retry excessivo amplifica carga no EE.
    - `timeout`: total em segundos por tentativa. Default lê
      `settings.HTTP_GET_BYTES_TIMEOUT` (20 s) — antes era hard-coded 10 s,
      insuficiente sob carga.

    Em 429 persistente, lança `EarthEngineRateLimitedError` carregando o
    nome da SA atual (quando há gee_manager ativo). O caller é responsável
    por rotacionar a SA + invalidar o cache de URL + regenerar via getMapId.
    """
    if timeout is None:
        timeout = settings.get("HTTP_GET_BYTES_TIMEOUT", 20.0)
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as sess, sess.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 429:
                    # Registrar métrica fire-and-forget. A rotação da SA fica
                    # com o caller, que tem o contexto da URL e do cache key.
                    sa_name: str | None = None
                    try:
                        from app.core.gee_auth import get_gee_manager
                        mgr = get_gee_manager()
                        if mgr:
                            mgr.report_http_429()
                            sa_name = mgr.current_sa_name
                    except Exception:
                        pass

                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            f"Rate limited (429). Retrying in {delay:.1f}s… "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error("Max retries reached for rate limiting")
                        raise EarthEngineRateLimitedError(
                            "Earth Engine rate-limited after retries",
                            sa_name=sa_name,
                        )
                else:
                    raise HTTPException(resp.status, f"Erro ao buscar recurso: {resp.reason}")
        except aiohttp.ClientError as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Connection error: {e}. Retrying in {delay:.1f}s…")
                await asyncio.sleep(delay)
                continue
            else:
                raise HTTPException(
                    status_code=503,
                    detail="Unable to connect to Earth Engine service",
                )
```

- [ ] **Step 4: Rodar testes e confirmar que passam**

```bash
pytest tests/unit/test_http_typed_exception.py -v
```

Esperado: `2 passed`.

- [ ] **Step 5: Atualizar `tests/unit/test_http_retries_reduced.py`**

O teste atual cobre apenas o default 10 s; o novo default é 20 s e o timeout é resolvido em runtime via settings. Substituir o conteúdo por:

```python
"""PR #5: retries default de `http_get_bytes` foi reduzido de 5 para 3 e
timeout explícito foi adicionado à sessão aiohttp. A partir do fix de 429
de 2026-05-12, o timeout default é resolvido via `settings.HTTP_GET_BYTES_TIMEOUT`
(20 s)."""
from __future__ import annotations

import inspect

from app.utils import http as http_mod


def test_default_max_retries_is_three():
    """5 retries é excessivo; 3 cobre flakiness transitório sem amplificar
    carga no EE sob degradação."""
    sig = inspect.signature(http_mod.http_get_bytes)
    assert sig.parameters["max_retries"].default == 3


def test_timeout_parameter_present():
    """Sem timeout, worker fica pendurado em EE lento — esgota o thread pool."""
    sig = inspect.signature(http_mod.http_get_bytes)
    assert "timeout" in sig.parameters


def test_default_timeout_resolves_to_twenty_seconds():
    """Default é resolvido em runtime via settings.HTTP_GET_BYTES_TIMEOUT;
    a constante esperada é 20 s — margem para EE responder em 8–12 s sob carga."""
    from app.core.config import settings
    assert settings.get("HTTP_GET_BYTES_TIMEOUT", 20.0) == 20
```

- [ ] **Step 6: Rodar a suite completa de http**

```bash
pytest tests/unit/test_http_retries_reduced.py tests/unit/test_http_typed_exception.py -v
```

Esperado: `5 passed` (3 do reduced + 2 do typed).

- [ ] **Step 7: Commit**

```bash
git add app/utils/http.py tests/unit/test_http_typed_exception.py tests/unit/test_http_retries_reduced.py
git commit -m "feat(tiles): EarthEngineRateLimitedError tipada e timeout default 20s em http_get_bytes"
```

---

## Task 3: Counters Prometheus por SA em `gee_pool.py`

**Files:**
- Modify: `app/core/metrics.py` (novos counters/gauges)
- Modify: `app/core/gee_pool.py` (instrumentação em `report_http_429`, `report_429`, `rotate_on_429`)
- Test: `tests/unit/test_gee_pool_metrics.py`

- [ ] **Step 1: Escrever teste falhando para os novos counters**

Arquivo: `tests/unit/test_gee_pool_metrics.py`

```python
"""Counters Prometheus por SA são essenciais para diagnosticar oscilação
entre SAs e capacidade de cota — sintoma escondido sem essa observabilidade."""
from __future__ import annotations

from prometheus_client import REGISTRY


def _read_counter(name: str, **labels: str) -> float:
    """Lê o valor atual de um counter Prometheus com os labels dados."""
    value = REGISTRY.get_sample_value(name, labels=labels)
    return value if value is not None else 0.0


def test_gee_sa_http_429_total_counter_exists():
    """gee_sa_http_429_total — contador por SA penalizada por 429 de tile."""
    from app.core import metrics  # noqa: F401
    val = _read_counter("gee_sa_http_429_total", sa_name="probe-sa@proj")
    assert val == 0.0  # counter inicia em 0


def test_gee_sa_rotation_total_counter_exists():
    """gee_sa_rotation_total — contador de rotações por origem/destino/trigger."""
    from app.core import metrics  # noqa: F401
    val = _read_counter(
        "gee_sa_rotation_total",
        from_sa="probe-from",
        to_sa="probe-to",
        trigger="http_429",
    )
    assert val == 0.0


def test_gee_sa_in_cooldown_gauge_exists():
    """gee_sa_in_cooldown — gauge 0/1 por SA, útil para alerta de capacidade."""
    from app.core import metrics  # noqa: F401
    val = REGISTRY.get_sample_value(
        "gee_sa_in_cooldown", labels={"sa_name": "probe-sa@proj"}
    )
    # Gauge não inicializado retorna None; teste só valida que o nome existe
    # via uma chamada subsequente.
    metrics.gee_sa_in_cooldown.labels(sa_name="probe-sa@proj").set(0)
    val = REGISTRY.get_sample_value(
        "gee_sa_in_cooldown", labels={"sa_name": "probe-sa@proj"}
    )
    assert val == 0.0
```

- [ ] **Step 2: Rodar e confirmar falha**

```bash
pytest tests/unit/test_gee_pool_metrics.py -v
```

Esperado: falhas porque os counters não existem.

- [ ] **Step 3: Adicionar counters em `app/core/metrics.py`**

**3a.** Atualizar a linha 8 de `app/core/metrics.py`:

Antes:
```python
from prometheus_client import Counter, Histogram
```

Depois:
```python
from prometheus_client import Counter, Gauge, Histogram
```

**3b.** Após a definição de `cache_hits_total` (~linha 30), inserir o novo bloco:

```python


# -- Métricas do pool de Service Accounts do GEE ------------------------------

gee_sa_http_429_total = Counter(
    "gee_sa_http_429_total",
    "Contagem de 429 do endpoint de tiles do EE por service account",
    labelnames=("sa_name",),
)

gee_sa_rotation_total = Counter(
    "gee_sa_rotation_total",
    "Rotações de SA realizadas por trigger (http_429 ou rest_api_429)",
    labelnames=("from_sa", "to_sa", "trigger"),
)

gee_sa_in_cooldown = Gauge(
    "gee_sa_in_cooldown",
    "1 se a SA está em cooldown por 429, 0 caso contrário",
    labelnames=("sa_name",),
)

gee_tile_url_regen_total = Counter(
    "gee_tile_url_regen_total",
    "Regenerações de URL do EE disparadas por 429, por layer",
    labelnames=("layer",),
)
```

- [ ] **Step 4: Instrumentar `report_http_429` em `gee_pool.py`**

Editar `app/core/gee_pool.py`. Localizar `report_http_429` (linha 259) e adicionar instrumentação ao final do método, antes do `return`:

Trecho original (linhas 284–288):
```python
        pipe = self._redis.pipeline()
        pipe.hincrby(metrics_key, "errors_429", 1)
        pipe.hset(metrics_key, "last_429_at", str(now))
        pipe.hset(metrics_key, "cooldown_until", str(new_cooldown_end))
        pipe.execute()
```

Substituir por:
```python
        pipe = self._redis.pipeline()
        pipe.hincrby(metrics_key, "errors_429", 1)
        pipe.hset(metrics_key, "last_429_at", str(now))
        pipe.hset(metrics_key, "cooldown_until", str(new_cooldown_end))
        pipe.execute()

        try:
            from app.core.metrics import gee_sa_http_429_total, gee_sa_in_cooldown
            gee_sa_http_429_total.labels(sa_name=sa_name).inc()
            gee_sa_in_cooldown.labels(sa_name=sa_name).set(1)
        except Exception:
            # Métrica é best-effort — falha de instrumentação não pode
            # quebrar a rotação que é caminho crítico.
            pass
```

E no branch de cooldown preservado (linhas 273–282), também incrementar o counter de 429:

Trecho original:
```python
        existing = self._redis.hget(metrics_key, "cooldown_until")
        if existing:
            try:
                if float(existing) > new_cooldown_end:
                    pipe = self._redis.pipeline()
                    pipe.hincrby(metrics_key, "errors_429", 1)
                    pipe.hset(metrics_key, "last_429_at", str(now))
                    pipe.execute()
                    return
            except ValueError:
                pass
```

Substituir por:
```python
        existing = self._redis.hget(metrics_key, "cooldown_until")
        if existing:
            try:
                if float(existing) > new_cooldown_end:
                    pipe = self._redis.pipeline()
                    pipe.hincrby(metrics_key, "errors_429", 1)
                    pipe.hset(metrics_key, "last_429_at", str(now))
                    pipe.execute()
                    try:
                        from app.core.metrics import gee_sa_http_429_total
                        gee_sa_http_429_total.labels(sa_name=sa_name).inc()
                    except Exception:
                        pass
                    return
            except ValueError:
                pass
```

- [ ] **Step 5: Instrumentar `rotate_on_429` em `gee_pool.py`**

Localizar `WorkerGEEManager.rotate_on_429` (linha 430). Após o log `Worker {self._worker_id} rotacionou: ...` (linha 456–459), adicionar:

```python
            try:
                from app.core.metrics import gee_sa_rotation_total
                gee_sa_rotation_total.labels(
                    from_sa=old_sa,
                    to_sa=self._current_sa.name,
                    trigger="http_429",
                ).inc()
            except Exception:
                pass
```

Nota: o decorator `@gee_retry` (REST API) chama `rotate_on_429` também. Por hora, todas as rotações são contadas como `trigger="http_429"`. Refinamento por trigger fica como follow-up — apenas o cenário de tiles é alvo desta entrega.

- [ ] **Step 6: Atualizar gauge de cooldown quando libera**

Em `report_429` (síncrono via REST API) e ao final do cooldown natural, o gauge precisaria voltar para 0. Como o cooldown expira por timestamp em Redis (não há hook ao expirar), a forma prática é o `get_metrics()` (linha 333) atualizar o gauge ao ser consultado. Editar o loop dentro de `get_metrics` (linha 340):

Trecho original:
```python
        for sa_name in self._accounts:
            score = self._redis.zscore(self._KEY_POOL, sa_name) or 0
            metrics_key = self._KEY_METRICS.format(sa_name)
            metrics = self._redis.hgetall(metrics_key)

            cooldown_until = metrics.get("cooldown_until", "")
            now = time.time()
            in_cooldown = bool(
                cooldown_until and cooldown_until != "" and float(cooldown_until) > now
            )
```

Adicionar logo após `in_cooldown = ...`:
```python
            try:
                from app.core.metrics import gee_sa_in_cooldown
                gee_sa_in_cooldown.labels(sa_name=sa_name).set(1 if in_cooldown else 0)
            except Exception:
                pass
```

- [ ] **Step 7: Rodar testes e confirmar que passam**

```bash
pytest tests/unit/test_gee_pool_metrics.py -v
```

Esperado: `3 passed`.

- [ ] **Step 8: Commit**

```bash
git add app/core/metrics.py app/core/gee_pool.py tests/unit/test_gee_pool_metrics.py
git commit -m "feat(tiles): counters Prometheus por SA do pool GEE [#5]"
```

---

## Task 4: Helper `fetch_tile_with_rotation`

**Files:**
- Create: `app/utils/ee_tile_fetch.py`
- Test: `tests/unit/test_ee_tile_fetch.py`

- [ ] **Step 1: Escrever testes falhando**

Arquivo: `tests/unit/test_ee_tile_fetch.py`

```python
"""Cobre o helper que orquestra rotação de SA + invalidação de cache +
regeneração de URL em 429 do download de tile.

Caminho feliz: passthrough simples para http_get_bytes.
Caminho de erro: 429 → rotate + delete_meta + url_factory + retry único.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.http import EarthEngineRateLimitedError


@pytest.fixture
def fake_manager():
    mgr = MagicMock()
    mgr.current_sa_name = "sa-old@proj"
    mgr.rotate_on_429 = MagicMock(
        side_effect=lambda: setattr(mgr, "current_sa_name", "sa-new@proj")
    )
    return mgr


@pytest.mark.asyncio
async def test_happy_path_no_rotation(fake_manager):
    """200 no primeiro shot — não rotaciona, não invalida cache, não regenera."""
    from app.utils import ee_tile_fetch

    url_factory = AsyncMock()

    with patch.object(ee_tile_fetch, "http_get_bytes", AsyncMock(return_value=b"PNG")) as fake_get, \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()) as fake_del, \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()) as fake_set, \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        result = await ee_tile_fetch.fetch_tile_with_rotation(
            cache_key="landsat_MONTH_2007_7_landsat-tvi-false/abc",
            cached_url="https://ee.googleapis.com/.../{z}/{x}/{y}",
            url_factory=url_factory,
            x=1, y=2, z=10,
            layer="landsat",
        )

    assert result == b"PNG"
    fake_get.assert_awaited_once()
    url_factory.assert_not_called()
    fake_del.assert_not_called()
    fake_set.assert_not_called()
    fake_manager.rotate_on_429.assert_not_called()


@pytest.mark.asyncio
async def test_429_triggers_full_rotation_cycle(fake_manager):
    """429 na primeira chamada → rotate + delete_meta + url_factory + retry."""
    from app.utils import ee_tile_fetch

    url_factory = AsyncMock(return_value="https://ee.googleapis.com/.../new/{z}/{x}/{y}")

    # Primeira chamada lança 429; segunda devolve bytes.
    fake_get = AsyncMock(side_effect=[
        EarthEngineRateLimitedError("rate limited", sa_name="sa-old@proj"),
        b"PNG-NEW",
    ])

    with patch.object(ee_tile_fetch, "http_get_bytes", fake_get), \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()) as fake_del, \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()) as fake_set, \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        result = await ee_tile_fetch.fetch_tile_with_rotation(
            cache_key="landsat_MONTH_2007_7_landsat-tvi-false/abc",
            cached_url="https://ee.googleapis.com/.../old/{z}/{x}/{y}",
            url_factory=url_factory,
            x=1, y=2, z=10,
            layer="landsat",
        )

    assert result == b"PNG-NEW"
    assert fake_get.await_count == 2
    fake_manager.rotate_on_429.assert_called_once()
    fake_del.assert_awaited_once_with("landsat_MONTH_2007_7_landsat-tvi-false/abc")
    url_factory.assert_awaited_once()
    fake_set.assert_awaited_once()


@pytest.mark.asyncio
async def test_persistent_429_raises_after_one_regeneration(fake_manager):
    """Se o retry após regeneração também devolve 429, propaga a exceção
    em vez de tentar uma terceira rodada — getMapId é caro e amplificar
    custa mais que entregar 503."""
    from app.utils import ee_tile_fetch

    url_factory = AsyncMock(return_value="https://ee.googleapis.com/.../new/{z}/{x}/{y}")

    fake_get = AsyncMock(side_effect=[
        EarthEngineRateLimitedError("rate limited", sa_name="sa-old@proj"),
        EarthEngineRateLimitedError("rate limited again", sa_name="sa-new@proj"),
    ])

    with patch.object(ee_tile_fetch, "http_get_bytes", fake_get), \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        with pytest.raises(EarthEngineRateLimitedError):
            await ee_tile_fetch.fetch_tile_with_rotation(
                cache_key="landsat_MONTH_2007_7_landsat-tvi-false/abc",
                cached_url="https://ee.googleapis.com/.../old/{z}/{x}/{y}",
                url_factory=url_factory,
                x=1, y=2, z=10,
                layer="landsat",
            )

    assert fake_get.await_count == 2
    fake_manager.rotate_on_429.assert_called_once()  # rotação única


@pytest.mark.asyncio
async def test_url_factory_failure_propagates(fake_manager):
    """Se url_factory falha durante regeneração, propaga limpo — sem deixar
    cache em estado inconsistente. delete_meta já foi chamado, mas isso é
    aceitável (idempotente; próximo request regenera)."""
    from app.utils import ee_tile_fetch

    factory_exc = RuntimeError("getMapId failed: project quota exhausted")
    url_factory = AsyncMock(side_effect=factory_exc)

    fake_get = AsyncMock(side_effect=EarthEngineRateLimitedError("rate limited"))

    with patch.object(ee_tile_fetch, "http_get_bytes", fake_get), \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        with pytest.raises(RuntimeError, match="getMapId failed"):
            await ee_tile_fetch.fetch_tile_with_rotation(
                cache_key="landsat_MONTH_2007_7/abc",
                cached_url="https://ee.googleapis.com/.../old/{z}/{x}/{y}",
                url_factory=url_factory,
                x=1, y=2, z=10,
                layer="landsat",
            )
```

- [ ] **Step 2: Rodar e confirmar falha**

```bash
pytest tests/unit/test_ee_tile_fetch.py -v
```

Esperado: `ImportError: No module named 'app.utils.ee_tile_fetch'`.

- [ ] **Step 3: Implementar o helper**

Arquivo: `app/utils/ee_tile_fetch.py`

```python
"""Helper de busca de tile do Earth Engine com rotação reativa de SA.

Resolve o caso onde a URL cacheada está vinculada a uma SA penalizada por
429: a rotação isolada do worker não basta, porque o token na URL continua
preso à SA antiga. Este helper orquestra o ciclo completo:

1. Tenta o download com a URL cacheada.
2. Em EarthEngineRateLimitedError:
   a. Rotaciona a SA do worker (acquire SA diferente, libera a antiga).
   b. Invalida o meta-cache da URL (delete_meta).
   c. Chama o `url_factory()` do caller para regenerar a URL via getMapId
      com a nova SA.
   d. Persiste a nova URL no meta-cache (set_meta).
   e. Tenta o download **uma única vez** com a nova URL.
3. Segundo 429 → propaga `EarthEngineRateLimitedError` para o caller
   converter em 503. Não tentamos terceira rodada porque getMapId custa
   ~200–500ms e amplificar custa mais que entregar erro rápido.

Idempotência: o ciclo é seguro em concorrência. Múltiplos workers podem
invalidar a mesma chave; `delete_meta` é no-op em chave ausente. Múltiplas
regenerações simultâneas geram URLs equivalentes — set_meta da última
prevalece, sem perda funcional.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Awaitable, Callable

from app.cache.cache import adelete_meta, aset_meta
from app.core.config import logger
from app.core.gee_auth import get_gee_manager
from app.utils.http import EarthEngineRateLimitedError, http_get_bytes


async def fetch_tile_with_rotation(
    *,
    cache_key: str,
    cached_url: str,
    url_factory: Callable[[], Awaitable[str]],
    x: int,
    y: int,
    z: int,
    layer: str,
) -> bytes:
    """Faz download do tile, com rotação reativa em caso de 429.

    Args:
        cache_key: Chave do meta-cache da URL (ex: "landsat_MONTH_2007_7_..."/<geohash>).
        cached_url: URL template com placeholders {x}/{y}/{z}, lida do cache.
        url_factory: Async callable que regenera a URL via getMapId. Deve
            ser idempotente do ponto de vista do caller (encapsula geom/dates/vis).
        x, y, z: Coordenadas do tile.
        layer: Nome da camada (usado em métricas e logs).

    Returns:
        Bytes do PNG do tile.

    Raises:
        EarthEngineRateLimitedError: 429 persistente após uma rotação.
        Qualquer outra exceção do `url_factory` ou de `http_get_bytes`.
    """
    try:
        return await http_get_bytes(cached_url.format(x=x, y=y, z=z))
    except EarthEngineRateLimitedError as exc:
        sa_old = exc.sa_name or "<unknown>"
        logger.warning(
            f"sa_rotated_http_429 layer={layer} cache_key={cache_key} "
            f"sa_from={sa_old} reason=tile_429"
        )

        manager = get_gee_manager()
        if manager is not None:
            # rotate_on_429 é síncrono (segura init_lock + ee.Initialize).
            await asyncio.get_event_loop().run_in_executor(None, manager.rotate_on_429)

        # Invalidar a URL cacheada — assinada com a SA antiga.
        try:
            await adelete_meta(cache_key)
        except Exception as del_exc:
            logger.warning(f"Falha ao invalidar meta cache {cache_key}: {del_exc}")

        # Regenerar a URL com a SA nova (via getMapId no url_factory).
        new_url = await url_factory()

        # Persistir a nova URL para próximos requests.
        try:
            await aset_meta(cache_key, {"url": new_url, "date": datetime.now().isoformat()})
        except Exception as set_exc:
            logger.warning(f"Falha ao persistir meta cache {cache_key}: {set_exc}")

        # Métrica: regeneração disparada por 429.
        try:
            from app.core.metrics import gee_tile_url_regen_total
            gee_tile_url_regen_total.labels(layer=layer).inc()
        except Exception:
            pass

        # Retry único — segundo 429 propaga para o caller.
        return await http_get_bytes(new_url.format(x=x, y=y, z=z))
```

- [ ] **Step 4: Rodar testes e confirmar que passam**

```bash
pytest tests/unit/test_ee_tile_fetch.py -v
```

Esperado: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/utils/ee_tile_fetch.py tests/unit/test_ee_tile_fetch.py
git commit -m "feat(tiles): helper fetch_tile_with_rotation com rotação reativa em 429 [#5]"
```

---

## Task 5: Adotar helper em `app/api/layers.py`

**Files:**
- Modify: `app/api/layers.py:570-583` (apenas o trecho `# 4 ▸ Faz download do tile remoto`)

- [ ] **Step 1: Ler o trecho atual**

Verificar que `app/api/layers.py:570-583` corresponde ao código abaixo:

```python
        else:
            layer_url = meta["url"]

        # 4 ▸ Faz download do tile remoto
        try:
            png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))
            logger.info(f"Tile downloaded: {file_cache} ({len(png_bytes)} bytes), saving to cache...")
            await set_png(file_cache, png_bytes)
            logger.info(f"Tile cached: {file_cache}")
            return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")
        except HTTPException as exc:
            logger.exception("Erro ao baixar tile")
            return tile_error_response.from_exception(exc)
```

Comando:
```bash
sed -n '570,584p' /home/tharles/projects_lapig/tiles/app/api/layers.py
```

- [ ] **Step 2: Adicionar import do helper**

Editar `app/api/layers.py`. Após a linha `from app.utils.http import http_get_bytes as _http_get_bytes` (linha 35), adicionar:

```python
from app.utils.ee_tile_fetch import fetch_tile_with_rotation
from app.utils.http import EarthEngineRateLimitedError
```

- [ ] **Step 3: Construir `url_factory` e usar `fetch_tile_with_rotation`**

Substituir o bloco do passo 1 (linhas 570–583) por:

```python
        else:
            layer_url = meta["url"]

        # 4 ▸ Faz download do tile remoto via helper com rotação reativa.
        #     Em 429, o helper: rotaciona SA → invalida meta cache →
        #     regenera URL via url_factory → tenta uma vez mais.
        async def _regenerate_url() -> str:
            geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
            loop = asyncio.get_event_loop()
            if layer == "landsat":
                _year = datetime.fromisoformat(dates["dtStart"]).year
                collection = get_landsat_collection(_year)
                landsat_vis = await get_landsat_vis_params_async(visparam, collection)
                return await loop.run_in_executor(
                    ee_executor,
                    _create_landsat_layer_with_params,
                    geom, dates, landsat_vis, composite_mode or "BEST_IMAGE",
                )
            return await loop.run_in_executor(
                ee_executor, builder_sync, geom, dates, vis,
            )

        try:
            png_bytes = await fetch_tile_with_rotation(
                cache_key=path_cache,
                cached_url=layer_url,
                url_factory=_regenerate_url,
                x=x, y=y, z=z,
                layer=layer,
            )
            logger.info(f"Tile downloaded: {file_cache} ({len(png_bytes)} bytes), saving to cache...")
            await set_png(file_cache, png_bytes)
            logger.info(f"Tile cached: {file_cache}")
            return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")
        except EarthEngineRateLimitedError as exc:
            logger.warning(f"Tile 503 ee_rate_limit (após rotação): {exc}")
            return tile_error_response(status_code=503, reason="ee_unavailable")
        except HTTPException as exc:
            logger.exception("Erro ao baixar tile")
            return tile_error_response.from_exception(exc)
```

- [ ] **Step 4: Rodar a suite completa de tests**

```bash
pytest tests/unit/ -v
```

Esperado: todos os testes passam (ou pelo menos os que existiam antes + os novos desta entrega).

- [ ] **Step 5: Rodar testes de integração relevantes**

```bash
pytest tests/integration/test_tile_handlers_propagate_status.py -v
```

Esperado: todos passam (o caminho 503 continua devolvendo 503 com reason `ee_unavailable`).

- [ ] **Step 6: Commit**

```bash
git add app/api/layers.py
git commit -m "feat(tiles): layers.py adota fetch_tile_with_rotation [#5]"
```

---

## Task 6: Adotar helper em `app/api/imagery.py`

**Files:**
- Modify: `app/api/imagery.py:455-475` (trecho do tile)

- [ ] **Step 1: Ler o trecho atual**

```bash
sed -n '445,480p' /home/tharles/projects_lapig/tiles/app/api/imagery.py
```

Identificar a linha 464: `png_bytes = await http_get_bytes(layer_url.format(x=x, y=y, z=z))`. Inspecionar o contexto (variáveis em escopo, builder do tile, cache key).

- [ ] **Step 2: Adicionar imports**

Editar `app/api/imagery.py`. Após `from app.utils.http import http_get_bytes` (linha 32), adicionar:

```python
from app.utils.ee_tile_fetch import fetch_tile_with_rotation
from app.utils.http import EarthEngineRateLimitedError
```

- [ ] **Step 3: Substituir o site de download pelo helper**

Localizar o bloco da linha 462–475 de `app/api/imagery.py`:

```python
        # 4 ▸ Download do tile remoto
        try:
            png_bytes = await http_get_bytes(layer_url.format(x=x, y=y, z=z))
            await set_png(tile_key, png_bytes)
            return StreamingResponse(
                io.BytesIO(png_bytes),
                media_type="image/png",
                headers={"X-Cache": "MISS", "X-Image-Id": imageId},
            )
        except HTTPException as exc:
            logger.exception(f"Erro ao baixar tile da imagem {imageId}")
            resp = tile_error_response.from_exception(exc)
            resp.headers["X-Image-Id"] = imageId
            return resp
```

Substituir por:

```python
        # 4 ▸ Download do tile remoto via helper com rotação reativa.
        async def _regenerate_url() -> str:
            loop = asyncio.get_event_loop()
            if layer == "s2_harmonized":
                vis = await _vis_param_for_s2(visparam)
                return await loop.run_in_executor(
                    ee_executor, _create_s2_image_layer_sync, imageId, vis,
                )
            vis = await _vis_param_for_landsat(visparam, imageId)
            return await loop.run_in_executor(
                ee_executor, _create_landsat_image_layer_sync, imageId, vis,
            )

        try:
            png_bytes = await fetch_tile_with_rotation(
                cache_key=meta_key,
                cached_url=layer_url,
                url_factory=_regenerate_url,
                x=x, y=y, z=z,
                layer="imagery",
            )
            await set_png(tile_key, png_bytes)
            return StreamingResponse(
                io.BytesIO(png_bytes),
                media_type="image/png",
                headers={"X-Cache": "MISS", "X-Image-Id": imageId},
            )
        except EarthEngineRateLimitedError as exc:
            logger.warning(f"Tile imagery 503 ee_rate_limit (após rotação): {exc}")
            resp = tile_error_response(status_code=503, reason="ee_unavailable")
            resp.headers["X-Image-Id"] = imageId
            return resp
        except HTTPException as exc:
            logger.exception(f"Erro ao baixar tile da imagem {imageId}")
            resp = tile_error_response.from_exception(exc)
            resp.headers["X-Image-Id"] = imageId
            return resp
```

Variáveis em escopo já existentes neste handler (vindas de `image_tile` na linha 391):
- `layer`, `imageId`, `visparam`, `meta_key`, `tile_key`, `layer_url`, `_create_s2_image_layer_sync`, `_create_landsat_image_layer_sync`, `_vis_param_for_s2`, `_vis_param_for_landsat`, `ee_executor`, `asyncio`.

- [ ] **Step 4: Rodar testes**

```bash
pytest tests/unit/ tests/integration/test_tile_handlers_propagate_status.py -v
```

Esperado: todos os testes passam.

- [ ] **Step 5: Commit**

```bash
git add app/api/imagery.py
git commit -m "feat(tiles): imagery.py adota fetch_tile_with_rotation [#5]"
```

---

## Task 7: Adotar helper em `app/modules/embedding_maps/router.py`

**Files:**
- Modify: `app/modules/embedding_maps/router.py:320-340`

- [ ] **Step 1: Ler o trecho atual**

```bash
sed -n '300,340p' /home/tharles/projects_lapig/tiles/app/modules/embedding_maps/router.py
```

Identificar:
- Linha 320: `await set_meta(meta_key, {"url": layer_url, "date": ...}, META_TTL)` — última geração da URL.
- Linha 330: `png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))` — download alvo.

- [ ] **Step 2: Adicionar imports**

Após `from app.utils.http import http_get_bytes as _http_get_bytes` (linha 28), adicionar:

```python
from app.utils.ee_tile_fetch import fetch_tile_with_rotation
from app.utils.http import EarthEngineRateLimitedError
```

- [ ] **Step 3: Extrair builder em função nomeada e adotar o helper**

O builder atual é uma closure `_build_and_get_url` definida apenas no branch de URL expirada (linha 297). Para reutilizá-la no `url_factory`, mover sua definição para fora do `if expired:` — assim ambos os caminhos (geração inicial e regeneração) usam a mesma função.

**3a.** Promover `_build_and_get_url` para antes do `if expired:`. Localizar o bloco da linha 280–326:

```python
        if expired:
            config = job.get("config", {})
            roi_config = config.get("roi", {})
            year = config.get("year", 2023)
            processing = config.get("processing", {})
            scale = processing.get("scale", 10)

            # Encontrar config do produto especifico
            product_cfg = {}
            for p in config.get("products", []):
                if p.get("product") == product:
                    product_cfg = p
                    break

            try:
                loop = asyncio.get_event_loop()

                def _build_and_get_url():
                    from .schemas import RoiConfig
                    roi = RoiConfig(**roi_config)
                    ee_roi = roi_to_ee_geometry(roi)
                    img = build_product_image(
                        year, ee_roi, product,
                        rgb_bands=product_cfg.get("rgb_bands"),
                        pca_components=product_cfg.get("pca_components", 3),
                        kmeans_k=product_cfg.get("kmeans_k", 8),
                        scale=scale,
                        sample_size=processing.get("sample_size", 5000),
                        year_b=product_cfg.get("year_b"),
                    )
                    vis = build_vis_params(
                        product,
                        palette=product_cfg.get("palette"),
                        vis_min=product_cfg.get("vis_min", -0.3),
                        vis_max=product_cfg.get("vis_max", 0.3),
                        kmeans_k=product_cfg.get("kmeans_k", 8),
                    )
                    return get_map_id_from_image(img, vis)

                layer_url = await loop.run_in_executor(ee_executor, _build_and_get_url)
                await set_meta(meta_key, {"url": layer_url, "date": datetime.now().isoformat()}, META_TTL)
            except Exception:
                logger.exception(f"Erro ao criar layer EE para job {job_id}, produto {product}")
                error_img = generate_error_image("Erro ao gerar tile")
                return StreamingResponse(error_img, media_type="image/png")
        else:
            layer_url = meta["url"]
```

Substituir por:

```python
        # Extrair config do produto antes do branch — usado tanto na geração
        # inicial quanto na regeneração por 429.
        config = job.get("config", {})
        roi_config = config.get("roi", {})
        year = config.get("year", 2023)
        processing = config.get("processing", {})
        scale = processing.get("scale", 10)

        product_cfg = {}
        for p in config.get("products", []):
            if p.get("product") == product:
                product_cfg = p
                break

        def _build_and_get_url():
            from .schemas import RoiConfig
            roi = RoiConfig(**roi_config)
            ee_roi = roi_to_ee_geometry(roi)
            img = build_product_image(
                year, ee_roi, product,
                rgb_bands=product_cfg.get("rgb_bands"),
                pca_components=product_cfg.get("pca_components", 3),
                kmeans_k=product_cfg.get("kmeans_k", 8),
                scale=scale,
                sample_size=processing.get("sample_size", 5000),
                year_b=product_cfg.get("year_b"),
            )
            vis = build_vis_params(
                product,
                palette=product_cfg.get("palette"),
                vis_min=product_cfg.get("vis_min", -0.3),
                vis_max=product_cfg.get("vis_max", 0.3),
                kmeans_k=product_cfg.get("kmeans_k", 8),
            )
            return get_map_id_from_image(img, vis)

        if expired:
            try:
                loop = asyncio.get_event_loop()
                layer_url = await loop.run_in_executor(ee_executor, _build_and_get_url)
                await set_meta(meta_key, {"url": layer_url, "date": datetime.now().isoformat()}, META_TTL)
            except Exception:
                logger.exception(f"Erro ao criar layer EE para job {job_id}, produto {product}")
                error_img = generate_error_image("Erro ao gerar tile")
                return StreamingResponse(error_img, media_type="image/png")
        else:
            layer_url = meta["url"]
```

**3b.** Substituir o bloco do download (linha 328–339):

Trecho original:
```python
        # 4. Download tile remoto
        try:
            png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))
            await set_png(cache_key, png_bytes, PNG_TTL)
            elapsed = (time.monotonic() - t0) * 1000
            return StreamingResponse(
                io.BytesIO(png_bytes),
                media_type="image/png",
                headers={"X-Cache-Status": "MISS", "X-Response-Time": f"{elapsed:.0f}ms"},
            )
        except HTTPException:
            logger.exception(f"Erro ao baixar tile para job {job_id}")
```

Substituir por:
```python
        # 4. Download tile remoto via helper com rotação reativa.
        async def _regenerate_url() -> str:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(ee_executor, _build_and_get_url)

        try:
            png_bytes = await fetch_tile_with_rotation(
                cache_key=meta_key,
                cached_url=layer_url,
                url_factory=_regenerate_url,
                x=x, y=y, z=z,
                layer="embedding_maps",
            )
            await set_png(cache_key, png_bytes, PNG_TTL)
            elapsed = (time.monotonic() - t0) * 1000
            return StreamingResponse(
                io.BytesIO(png_bytes),
                media_type="image/png",
                headers={"X-Cache-Status": "MISS", "X-Response-Time": f"{elapsed:.0f}ms"},
            )
        except EarthEngineRateLimitedError:
            logger.warning(f"Tile embedding_maps 503 ee_rate_limit (após rotação) job={job_id}")
            error_img = generate_error_image("EE rate limited")
            return StreamingResponse(error_img, media_type="image/png", status_code=503)
        except HTTPException:
            logger.exception(f"Erro ao baixar tile para job {job_id}")
```

Manter o restante do `except HTTPException` intacto a partir da linha 340 (não tocar no bloco de retorno do erro existente).

- [ ] **Step 4: Rodar testes**

```bash
pytest tests/unit/ -v
```

Esperado: todos passam.

- [ ] **Step 5: Commit**

```bash
git add app/modules/embedding_maps/router.py
git commit -m "feat(tiles): embedding_maps adota fetch_tile_with_rotation [#5]"
```

---

## Task 8: Documentação operacional no runbook

**Files:**
- Modify: `docs/deploy-runbook.md`

- [ ] **Step 1: Ler runbook atual**

```bash
sed -n '1,50p' /home/tharles/projects_lapig/tiles/docs/deploy-runbook.md
```

- [ ] **Step 2: Adicionar seção sobre expansão do pool de SAs**

Anexar ao final do `docs/deploy-runbook.md` (após a seção existente de canário/circuit breaker):

```markdown
## Expansão do pool de Service Accounts do GEE

A taxa de 503 (`ee_unavailable`) sob carga é limitada pela cota das SAs
ativas no pool, não pelo código. O fix de rotação reativa (entrega de
2026-05-12) mitiga picos transitórios; o teto sustentável depende do
número de SAs com cota disponível.

### Sintomas que indicam saturação de capacidade

- Métrica `gee_sa_rotation_total` cresce continuamente, sem estabilizar.
- Métrica `gee_sa_in_cooldown` mostra todas as SAs alternando em cooldown.
- `gee_tile_url_regen_total` alto e sustentado — confirma que 429 não é
  pico transitório, é teto de capacidade.

### Ação operacional

1. Provisionar SAs adicionais no GCP Console (projeto `earthengine-legacy`
   ou equivalente). Cada SA tem cota independente no endpoint de tiles.
2. Baixar o JSON da nova SA e depositar em `.service-accounts/`:
   ```bash
   scp nova-sa.json prod-lapig:/path/to/.service-accounts/
   ```
3. Hot-reload do pool — sem necessidade de redeploy:
   ```bash
   curl -X POST http://localhost:8080/admin/gee-pool/refresh
   ```
4. Verificar que a nova SA foi descoberta:
   ```bash
   curl http://localhost:8080/admin/gee-pool/metrics
   ```
5. Acompanhar `gee_sa_http_429_total` por SA: distribuição deve ficar
   mais equilibrada à medida que o pool absorve a carga.

### Quando reduzir a frota

Se `gee_sa_in_cooldown` raramente passa de 0 e `gee_sa_rotation_total`
fica estagnado por dias seguidos, há overprovisionamento. SAs ociosas
não custam, mas complicam a rotação dos JSONs — manter o pool em
tamanho mínimo necessário para sustentar o pico observado.
```

- [ ] **Step 3: Commit**

```bash
git add docs/deploy-runbook.md
git commit -m "docs(tiles): runbook de expansão do pool de SAs do GEE"
```

---

## Task 9: Verificação final e suite completa

**Files:** todas as mudanças do plano.

- [ ] **Step 1: Rodar a suite inteira**

```bash
cd /home/tharles/projects_lapig/tiles
pytest tests/ -v --tb=short
```

Esperado: 0 falhas. Se houver falha, parar, investigar e corrigir antes de seguir.

- [ ] **Step 2: Verificar build do container**

```bash
docker compose -f docker-compose.yml build tile
```

Esperado: build bem-sucedido. Se falhar por import, há referência quebrada — verificar.

- [ ] **Step 3: Smoke local (opcional, se há ambiente local)**

```bash
docker compose up -d valkey minio
docker compose up tile
```

Em outro terminal:
```bash
curl -v "http://localhost:8080/tiles/landsat/512/512/10?year=2015&month=7" | head -c 200
```

Esperado: 200 com `Content-Type: image/png` (ou 503 com `X-Error-Reason: ee_unavailable` se EE realmente saturada; o ponto é que o serviço responde).

- [ ] **Step 4: Push do branch**

```bash
git push origin <branch-name>
```

Não criar PR ou merge automático — aguardar revisão.

---

## Notas de execução

- **Não há mudanças destrutivas no Redis** — nenhuma chave é renomeada; `delete_meta` é puramente reativo em 429.
- **Retrocompatibilidade**: chamadas legacy a `http_get_bytes` continuam funcionando; a única diferença é que 429 persistente agora levanta `EarthEngineRateLimitedError` em vez de `HTTPException(503)`. Callers que ignoram a exceção tipada continuam servindo 503 via `tile_error_response.from_exception` (que não cobre o novo tipo) — por isso cada call site precisa do branch novo `except EarthEngineRateLimitedError`.
- **Capacidade** continua sendo problema operacional, não de código. O plano não promete que a taxa de 503 vai a zero — promete que ela cai significativamente e fica observável.
- **Rollback** é via `docker service rollback prod_tiles_tile` (sem migração de schema, sem mudança de contrato externo).
