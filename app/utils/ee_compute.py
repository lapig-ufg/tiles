"""
Computação assíncrona do Earth Engine via REST API v1.

Substitui .getInfo() por chamadas HTTP diretas ao endpoint value:compute,
permitindo:
- True async (non-blocking) via aiohttp
- Credenciais por request (não depende de ee.Initialize global)
- Paralelismo real com asyncio.gather
- Integração com o pool de SAs para distribuição de cota

Uso:
    from app.utils.ee_compute import compute_value, compute_parallel

    # Substituir: result = expression.getInfo()
    result = await compute_value(expression)

    # Computar em paralelo:
    ndvi, precip = await compute_parallel(ndvi_expr, precip_expr)
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import aiohttp
import ee
import google.auth.transport.requests

from app.core.config import settings, logger


_BASE_URL = "https://earthengine.googleapis.com/v1"
_session: aiohttp.ClientSession | None = None


# ---------------------------------------------------------------------------
# Sessão HTTP reutilizável
# ---------------------------------------------------------------------------

async def _get_session() -> aiohttp.ClientSession:
    """Retorna a sessão HTTP compartilhada, criando-a se necessário."""
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=300)
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _session


async def close_session() -> None:
    """Fecha a sessão HTTP. Chamar no shutdown da aplicação."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ---------------------------------------------------------------------------
# Resolução de credenciais e projeto
# ---------------------------------------------------------------------------

def _resolve_credentials(credentials=None):
    """Obtém credenciais do worker atual se não fornecidas."""
    if credentials is not None:
        return credentials

    from app.core.gee_auth import get_gee_manager
    mgr = get_gee_manager()
    if mgr and mgr._current_sa:
        return mgr._current_sa.credentials

    raise RuntimeError(
        "Nenhuma credencial GEE disponível. "
        "Certifique-se de que o Earth Engine foi inicializado."
    )


def _resolve_project(credentials) -> str:
    """Determina o projeto GEE para chamadas REST API.

    Ordem de precedência:
    1. GEE_CLOUD_PROJECT no settings
    2. project_id das credenciais da SA
    3. Fallback para 'earthengine-legacy'
    """
    configured = settings.get("GEE_CLOUD_PROJECT")
    if configured:
        return configured

    project_id = getattr(credentials, "project_id", None)
    if project_id:
        return project_id

    return "earthengine-legacy"


async def _ensure_valid_token(credentials) -> None:
    """Garante que o token OAuth2 está válido, renovando se necessário."""
    if credentials.valid:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        credentials.refresh,
        google.auth.transport.requests.Request(),
    )


# ---------------------------------------------------------------------------
# Computação async — substituto de .getInfo()
# ---------------------------------------------------------------------------

async def compute_value(
    expression,
    *,
    credentials=None,
    project: str | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """Computa uma expressão EE de forma assíncrona via REST API v1.

    Substitui diretamente ``expression.getInfo()`` com true async.

    Args:
        expression: Objeto EE (ee.List, ee.Dictionary, ee.Number, etc.)
        credentials: Credenciais OAuth2. Se None, usa as do worker atual.
        project: GCP project ID. Se None, detecta automaticamente.
        max_retries: Máximo de tentativas em caso de 429/erro transitório.
        base_delay: Delay base para backoff exponencial (segundos).

    Returns:
        O resultado da computação (mesmo formato de ``.getInfo()``).

    Raises:
        ee.EEException: Em caso de erro permanente do Earth Engine.
    """
    creds = _resolve_credentials(credentials)
    proj = project or _resolve_project(creds)

    # Serializar expressão para Cloud API
    serialized = ee.serializer.encode(expression, for_cloud_api=True)

    url = f"{_BASE_URL}/projects/{proj}/value:compute"
    body = {"expression": serialized}

    session = await _get_session()

    for attempt in range(max_retries):
        await _ensure_valid_token(creds)

        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            # Header de projeto para billing — habilita cotas de high-volume
            # quando o projeto GCP tem billing ativo vinculado ao EE.
            "X-Goog-User-Project": proj,
        }

        try:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result")

                if resp.status == 429:
                    # Na 2ª tentativa 429 (attempt >= 1), rotacionar a SA do
                    # worker para a próxima tentativa — evita insistir na
                    # mesma conta sobrecarregada. Só rotaciona quando usamos
                    # a SA do pool (credentials não fornecida externamente).
                    rotated = False
                    if attempt >= 1 and credentials is None:
                        loop = asyncio.get_event_loop()
                        rotated = await loop.run_in_executor(None, _rotate_sa_on_429)
                        if rotated:
                            creds = _resolve_credentials(None)
                            new_proj = project or _resolve_project(creds)
                            if new_proj != proj:
                                proj = new_proj
                                url = f"{_BASE_URL}/projects/{proj}/value:compute"

                    if not rotated:
                        _report_429()

                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            f"EE REST API 429 "
                            f"(SA {'rotacionada' if rotated else 'mantida'}). "
                            f"Tentativa {attempt + 1}/{max_retries}, "
                            f"retry em {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        continue

                    raise ee.EEException(
                        f"Rate limit excedido após {max_retries} tentativas"
                    )

                if resp.status == 401:
                    # Token expirado — forçar refresh e retentar
                    creds.expiry = None
                    await _ensure_valid_token(creds)
                    continue

                # Outros erros
                error_text = await resp.text()
                raise ee.EEException(
                    f"EE REST API erro {resp.status}: {error_text}"
                )

        except aiohttp.ClientError as exc:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Erro de conexão EE REST: {exc}. Retry em {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue
            raise ee.EEException(f"Erro de conexão: {exc}")

    raise ee.EEException("Máximo de tentativas excedido para computação EE")


# ---------------------------------------------------------------------------
# Computação paralela
# ---------------------------------------------------------------------------

async def compute_parallel(*expressions, credentials=None, project: str | None = None) -> list:
    """Computa múltiplas expressões EE em paralelo.

    Todas as expressões são enviadas simultaneamente ao GEE.
    Ideal para paralelizar chamadas independentes que antes eram
    sequenciais com .getInfo().

    Args:
        *expressions: Objetos EE para computar em paralelo.
        credentials: Credenciais compartilhadas (ou None para worker atual).
        project: GCP project ID.

    Returns:
        Lista de resultados na mesma ordem das expressões.

    Exemplo:
        ndvi, precip = await compute_parallel(ndvi_expr, precip_expr)
    """
    tasks = [
        compute_value(expr, credentials=credentials, project=project)
        for expr in expressions
    ]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _report_429() -> None:
    """Registra métrica de 429 no pool de SAs (fire-and-forget)."""
    try:
        from app.core.gee_auth import get_gee_manager
        mgr = get_gee_manager()
        if mgr:
            mgr.report_http_429()
    except Exception:
        pass


def _rotate_sa_on_429() -> bool:
    """Solicita rotação de SA ao manager do worker. Retorna True se rotacionou.

    Executado em run_in_executor pois rotate_on_429 é síncrono e chama
    ee.Initialize(), bloqueante por alguns segundos.
    """
    try:
        from app.core.gee_auth import get_gee_manager
        mgr = get_gee_manager()
        if mgr:
            mgr.rotate_on_429()
            return True
    except Exception as exc:
        logger.warning(f"Falha ao rotacionar SA no 429 do REST API: {exc}")
    return False
