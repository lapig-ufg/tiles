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
    a constante esperada é 20 s — margem para EE responder em 8–12 s sob carga.

    Verifica que a chave existe no settings (não apenas que o fallback bate).
    Se HTTP_GET_BYTES_TIMEOUT for removido do settings.toml por engano,
    este teste deve falhar."""
    from app.core.config import settings
    # Sentinela único: detecta a ausência da chave (sem confundir com o default 20.0).
    _MISSING = object()
    value = settings.get("HTTP_GET_BYTES_TIMEOUT", _MISSING)
    assert value is not _MISSING, "HTTP_GET_BYTES_TIMEOUT deve estar definida em settings.toml"
    assert value == 20
