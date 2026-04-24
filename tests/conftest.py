"""Configuração mínima de testes.

Stubs de módulos externos (earthengine-api, valkey, pymongo, etc.) são injetados
antes de qualquer import do pacote `app` para permitir rodar testes unitários
sem dependências de produção.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TILES_ENV", "test")
os.environ.setdefault("SKIP_GEE_INIT", "true")


# Módulos que guardam estado global (prometheus registry, contextvars) e
# **não podem ser re-importados** durante um mesmo processo de teste.
# Fixtures de integração usam `reset_app_imports()` para limpar sys.modules
# preservando estes.
PROTECTED_MODULES: tuple[str, ...] = (
    "app.core.metrics",        # Counter/Histogram não permitem duplicate registration
    "app.middleware.request_id",  # ContextVar identity precisa ser estável entre fixtures
)


def reset_app_imports() -> None:
    """Remove módulos `app.*` de sys.modules para forçar re-import com stubs
    frescos. Preserva módulos globalmente estatefuis (ver PROTECTED_MODULES)."""
    for m in list(sys.modules):
        if not (m.startswith("app.") or m == "app"):
            continue
        if any(m == p or m.startswith(p + ".") for p in PROTECTED_MODULES):
            continue
        sys.modules.pop(m, None)


def _stub(name: str, attrs: dict | None = None) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, val in (attrs or {}).items():
        setattr(mod, key, val)
    sys.modules[name] = mod
    return mod


if "ee" not in sys.modules:
    ee = _stub("ee")

    class EEException(Exception):
        pass

    class _Chainable:
        """Objeto chainable de mentira — qualquer método/atributo retorna ele mesmo.
        Suficiente para os testes que só precisam que o EE-side não exploda até
        `ee.data.getMapId`, que é o ponto onde mockamos o comportamento real."""

        def __call__(self, *_a, **_k): return self
        def __getattr__(self, _name): return self

    _CHAIN = _Chainable()

    ee.EEException = EEException
    ee.Geometry = type("Geometry", (), {"BBox": staticmethod(lambda *_a, **_k: _CHAIN)})
    ee.Image = type("Image", (), {"constant": staticmethod(lambda *_a, **_k: _CHAIN)})
    ee.ImageCollection = lambda *_a, **_k: _CHAIN
    ee.Filter = type("Filter", (), {"lt": staticmethod(lambda *_a, **_k: _CHAIN),
                                     "eq": staticmethod(lambda *_a, **_k: _CHAIN),
                                     "inList": staticmethod(lambda *_a, **_k: _CHAIN),
                                     "notNull": staticmethod(lambda *_a, **_k: _CHAIN)})
    ee.Reducer = type("Reducer", (), {"min": staticmethod(lambda: _CHAIN)})
    ee.List = lambda *_a, **_k: _CHAIN
    ee.Algorithms = type("Algorithms", (), {"If": staticmethod(lambda *_a, **_k: _CHAIN)})

    data = _stub("ee.data")
    data.getMapId = lambda *_a, **_k: {"tile_fetcher": type("T", (), {"url_format": ""})()}
    ee.data = data
