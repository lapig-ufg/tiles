"""
Proxy reverso para COGs hospedados em servidores sem suporte a CORS.

Necessário para o BDC (data.inpe.br), cujos GeoTIFFs não enviam
Access-Control-Allow-Origin, impedindo o acesso direto via geotiff.js
no navegador.

Suporta HTTP Range requests (essencial para COGs — o geotiff.js faz
leituras parciais por offset/length) e HEAD requests (para detecção
de Accept-Ranges e tamanho do arquivo).
"""

import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter()

# Domínios permitidos para proxy — evita open relay
ALLOWED_HOSTS = frozenset([
    "data.inpe.br",
])

# Headers que devem ser repassados do servidor de origem
PASSTHROUGH_HEADERS = frozenset({
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "etag",
    "last-modified",
})

# Timeout generoso para COGs grandes
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# Cliente HTTP reutilizável (connection pooling).
# Lifecycle gerenciado via startup/shutdown do FastAPI (ver registro em router.py).
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def shutdown_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _validate_url(url: str) -> str:
    """
    Valida a URL contra a allowlist de hosts.

    Proteção SSRF: rejeita URLs com userinfo (user:pass@host),
    hosts fora da allowlist e esquemas não-HTTP(S).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="URL inválida")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="Apenas URLs HTTP/HTTPS são aceitas",
        )

    # Bloquear URLs com userinfo — vetor SSRF:
    # https://data.inpe.br@evil.com resolve para evil.com
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise HTTPException(
            status_code=400,
            detail="URLs com credenciais embutidas não são permitidas",
        )

    if parsed.hostname not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=f"Host não permitido: {parsed.hostname}",
        )

    return url


def _filter_response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    """Filtra e retorna apenas headers seguros do upstream."""
    headers: dict[str, str] = {}
    for key, value in upstream_headers.items():
        if key.lower() in PASSTHROUGH_HEADERS:
            headers[key] = value

    # Cache de 1 hora — COGs são dados estáticos
    headers["Cache-Control"] = "public, max-age=3600"
    return headers


@router.head("/cog-proxy")
async def cog_proxy_head(request: Request, url: str):
    """
    HEAD proxy — geotiff.js usa para detectar Accept-Ranges
    e o tamanho do arquivo antes de iniciar Range GETs.
    """
    target_url = _validate_url(url)
    client = get_client()

    try:
        upstream = await client.head(target_url)
    except httpx.RequestError as exc:
        logger.warning("COG proxy HEAD falhou: %s → %s", target_url, exc)
        raise HTTPException(status_code=502, detail="Erro ao conectar com o servidor de origem")

    if upstream.status_code >= 400:
        raise HTTPException(
            status_code=upstream.status_code,
            detail="Servidor de origem retornou erro",
        )

    return Response(
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
    )


@router.get("/cog-proxy")
async def cog_proxy(request: Request, url: str):
    """
    GET proxy com streaming — suporta Range requests para leituras
    parciais de COGs pelo geotiff.js/OpenLayers.

    Query params:
        url: URL completa do recurso remoto (deve pertencer a um host permitido).
    """
    target_url = _validate_url(url)
    client = get_client()

    # Repassar header Range do cliente
    upstream_headers: dict[str, str] = {}
    if "range" in request.headers:
        upstream_headers["Range"] = request.headers["range"]

    try:
        upstream = await client.send(
            client.build_request("GET", target_url, headers=upstream_headers),
            stream=True,
        )
    except httpx.RequestError as exc:
        logger.warning("COG proxy GET falhou: %s → %s", target_url, exc)
        raise HTTPException(status_code=502, detail="Erro ao conectar com o servidor de origem")

    if upstream.status_code >= 400:
        await upstream.aclose()
        raise HTTPException(
            status_code=upstream.status_code,
            detail="Servidor de origem retornou erro",
        )

    return StreamingResponse(
        content=upstream.aiter_bytes(chunk_size=65536),
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        background=upstream.aclose,
    )
