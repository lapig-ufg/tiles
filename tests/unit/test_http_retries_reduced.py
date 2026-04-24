"""PR #5: retries default de `http_get_bytes` foi reduzido de 5 para 3 e
timeout explícito foi adicionado à sessão aiohttp."""
from __future__ import annotations

import inspect

from app.utils import http as http_mod


def test_default_max_retries_is_three():
    """5 retries é excessivo; 3 cobre flakiness transitório sem amplificar
    carga no EE sob degradação."""
    sig = inspect.signature(http_mod.http_get_bytes)
    assert sig.parameters["max_retries"].default == 3


def test_explicit_timeout_parameter_present():
    """Sem timeout, worker pode ficar pendurado em EE lento indefinidamente —
    esgota o thread pool e trava o serviço."""
    sig = inspect.signature(http_mod.http_get_bytes)
    assert "timeout" in sig.parameters
    default_timeout = sig.parameters["timeout"].default
    # Deve ser finito e "agressivo" — 10s é o limite prático para um tile.
    assert default_timeout is not None
    assert 5 <= default_timeout <= 15
