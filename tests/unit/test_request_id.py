"""Request ID por requisição + propagação via contextvar para logs."""
from __future__ import annotations


def test_request_id_var_default_is_dash():
    """Fora de request, `request_id_var.get()` retorna '-' para não vazar estado."""
    from app.middleware.request_id import request_id_var
    assert request_id_var.get() == "-"


def test_request_id_var_set_and_reset_is_safe():
    """ContextVar set/reset deve ser idempotente — não vaza entre requests."""
    from app.middleware.request_id import request_id_var

    token = request_id_var.set("abc-123")
    assert request_id_var.get() == "abc-123"

    request_id_var.reset(token)
    assert request_id_var.get() == "-"
