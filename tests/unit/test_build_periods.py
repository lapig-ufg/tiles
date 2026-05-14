"""Contrato de `_build_periods` e `_filter_collection_by_periods`.

Ancora o novo formato (WET com 2 intervalos jan-mai ∪ nov-dez; DRY jun-out;
MONTH com 1 intervalo cobrindo o mês inteiro) e a semântica half-open
(dtEnd EXCLUSIVO, alinhado com `ee.ImageCollection.filterDate`).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def layers_module():
    from tests.conftest import reset_app_imports

    # Snapshot completo de app.* antes do teste. No teardown restauramos
    # exatamente o estado original — não basta `pop` ou `reset_app_imports`,
    # pois isso descarregaria classes como app.utils.http.EarthEngineRateLimitedError
    # cuja identidade outros testes mantêm em binds locais (`from X import Y`).
    snapshot_app = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}

    reset_app_imports()

    saved: dict[str, object | None] = {}

    def _inject(name: str, module):
        if name not in saved:
            saved[name] = sys.modules.get(name)
        sys.modules[name] = module

    # Stubs mínimos para permitir o import de app.api.layers
    cache_mod = type(sys)("app.cache.cache")
    async def _noop(*a, **k): return None
    async def _none(*a, **k): return None
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_lock(*_a, **_k):
        yield True

    cache_mod.aget_png = _none
    cache_mod.aset_png = _noop
    cache_mod.aget_meta = _none
    cache_mod.aset_meta = _noop
    cache_mod.adelete_meta = _noop
    cache_mod.atile_lock = _fake_lock
    cache_mod.PNG_TTL = 30 * 24 * 3600
    cache_mod.PNG_TTL_HISTORICAL = 365 * 24 * 3600
    _inject("app.cache.cache", cache_mod)

    rate_limiter_mod = type(sys)("app.middleware.rate_limiter")
    def _pt(*_a, **_k):
        def deco(fn): return fn
        return deco
    rate_limiter_mod.limit_sentinel = _pt
    rate_limiter_mod.limit_landsat = _pt
    _inject("app.middleware.rate_limiter", rate_limiter_mod)

    vpdb = type(sys)("app.visualization.vis_params_db")
    async def _vp(*a, **k): return {"bands": ["SR_B4"]}
    vpdb.get_landsat_vis_params_async = _vp
    vpdb.vis_params_manager = object()
    vpdb.get_visparams_dict = lambda: {}
    vpdb.get_landsat_collection = lambda y: "LANDSAT/LT05/C02/T1_L2"
    _inject("app.visualization.vis_params_db", vpdb)

    vpl = type(sys)("app.visualization.vis_params_loader")
    vpl.VISPARAMS = {}
    vpl.get_VISPARAMS_sync = lambda: {}
    async def _vps(*a, **k): return {}
    vpl.get_visparams = _vps
    vpl.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4"]}
    vpl.get_landsat_collection = lambda year: "LANDSAT/LT05/C02/T1_L2"
    _inject("app.visualization.vis_params_loader", vpl)

    caps = type(sys)("app.utils.capabilities")
    caps.get_capabilities_provider = lambda: type("P", (), {
        "get_capabilities": staticmethod(lambda *a, **k: {"collections": []}),
    })()
    _inject("app.utils.capabilities", caps)

    svc_tile = type(sys)("app.services.tile")
    svc_tile.tile2goehashBBOX = lambda x, y, z: ({"w": -50, "s": -10, "e": -49, "n": -9}, "abc")
    _inject("app.services.tile", svc_tile)

    gee_pool = type(sys)("app.core.gee_pool")
    gee_pool.gee_retry = lambda *a, **k: (lambda fn: fn)
    _inject("app.core.gee_pool", gee_pool)

    # NÃO stubbar app.utils.http e app.utils.ee_tile_fetch: ambos importam
    # limpos. Stubbá-los criaria classes EarthEngineRateLimitedError distintas
    # e quebraria testes vizinhos que dependem da identidade da classe real.

    from app.api import layers
    try:
        yield layers
    finally:
        # Restaura snapshot exato: remove o que veio do teste, devolve o
        # que existia antes. Preserva a identidade de classes carregadas
        # pelos outros módulos de teste (binds via `from X import Y`).
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                if name in snapshot_app:
                    sys.modules[name] = snapshot_app[name]
                else:
                    sys.modules.pop(name, None)
        for name, module in snapshot_app.items():
            sys.modules[name] = module


# ---------- _build_periods ----------

def test_wet_returns_two_disjoint_intervals(layers_module):
    """WET = [jan-mai) ∪ [nov-dez+1) — dois intervalos para cobrir início e fim do ano civil."""
    dates = layers_module._build_periods("WET", 2024, 0)
    assert isinstance(dates, list)
    assert len(dates) == 2
    assert dates[0] == {"dtStart": "2024-01-01", "dtEnd": "2024-06-01"}
    assert dates[1] == {"dtStart": "2024-11-01", "dtEnd": "2025-01-01"}


def test_dry_returns_single_interval_with_exclusive_end(layers_module):
    """DRY = [jun-out+1) — dtEnd EXCLUSIVO para cobrir outubro inteiro via filterDate."""
    dates = layers_module._build_periods("DRY", 2024, 0)
    assert isinstance(dates, list)
    assert len(dates) == 1
    assert dates[0] == {"dtStart": "2024-06-01", "dtEnd": "2024-11-01"}


def test_month_returns_single_interval_covering_full_month(layers_module):
    """MONTH cobre o mês inteiro; dtEnd EXCLUSIVO = primeiro dia do mês seguinte."""
    feb = layers_module._build_periods("MONTH", 2024, 2)  # ano bissexto
    assert feb == [{"dtStart": "2024-02-01", "dtEnd": "2024-03-01"}]

    dec = layers_module._build_periods("MONTH", 2024, 12)
    assert dec == [{"dtStart": "2024-12-01", "dtEnd": "2025-01-01"}]


def test_month_validates_range(layers_module):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        layers_module._build_periods("MONTH", 2024, 0)
    assert exc.value.status_code == 400


def test_invalid_period_raises_404(layers_module):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        layers_module._build_periods("INVALID", 2024, 1)
    assert exc.value.status_code == 404


# ---------- _filter_collection_by_periods ----------

def test_filter_collection_applies_filterdate_per_interval(layers_module):
    """Cada intervalo gera um `filterDate(start, end)`; o resultado é o merge dos N."""
    calls = []

    class FakeCollection:
        def __init__(self, name):
            self.name = name
        def filterDate(self, start, end):
            calls.append(("filterDate", start, end))
            return FakeCollection(f"{self.name}|{start}-{end}")
        def merge(self, other):
            calls.append(("merge", self.name, other.name))
            return FakeCollection(f"({self.name})+({other.name})")
        def filterBounds(self, geom):
            calls.append(("filterBounds", geom))
            return FakeCollection(f"{self.name}@{geom}")

    base = FakeCollection("base")
    dates = [
        {"dtStart": "2024-01-01", "dtEnd": "2024-06-01"},
        {"dtStart": "2024-11-01", "dtEnd": "2025-01-01"},
    ]
    result = layers_module._filter_collection_by_periods(base, dates, geom="GEOM")

    # 2 filterDate + 1 merge + 1 filterBounds
    assert ("filterDate", "2024-01-01", "2024-06-01") in calls
    assert ("filterDate", "2024-11-01", "2025-01-01") in calls
    assert any(c[0] == "merge" for c in calls)
    assert any(c[0] == "filterBounds" and c[1] == "GEOM" for c in calls)
    assert result is not None


def test_filter_collection_rejects_empty_dates(layers_module):
    with pytest.raises(ValueError):
        layers_module._filter_collection_by_periods(object(), [])
