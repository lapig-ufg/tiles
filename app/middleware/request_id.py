"""Request ID middleware + ContextVar para propagação em logs estruturados."""
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

# Valor default `"-"` (não string vazia) para evitar que formatters com
# `{extra[request_id]}` gerem espaços estranhos fora de contexto de request.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Lê `X-Request-ID` do header; gera UUID v4 se ausente.

    Propaga via `request_id_var` (contextvar) para que loguru e OTLP
    possam incluir o valor em cada record dentro da corrotina da request.
    Reposta ao cliente o mesmo ID no header de resposta para correlação
    ponta-a-ponta.
    """

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(self.header_name.lower()) or str(uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers[self.header_name] = rid
        return response
