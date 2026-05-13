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
