"""
Utilitário HTTP compartilhado para download de imagens do Earth Engine.
Extraído de layers.py para reuso entre módulos (tiles, imagery).
"""
from __future__ import annotations

import asyncio
import random

import aiohttp
from fastapi import HTTPException

from app.core.config import logger


async def http_get_bytes(
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: float = 10.0,
) -> bytes:
    """Faz download de bytes via HTTP GET com retry e backoff exponencial.

    Trata 429 (rate limit) com backoff exponencial + jitter.
    Retorna os bytes da resposta em caso de 200.

    Parâmetros:
    - `max_retries`: reduzido de 5 para 3 (PR #5) — retry excessivo amplifica
      carga no EE sob degradação sem melhorar taxa de sucesso.
    - `timeout`: total em segundos por tentativa. Sem timeout explícito o
      worker trava em EE lento, esgota o thread pool.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as sess, sess.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 429:
                    # Registrar métrica no pool (sem rotação — URL já gerada)
                    try:
                        from app.core.gee_auth import get_gee_manager
                        mgr = get_gee_manager()
                        if mgr:
                            mgr.report_http_429()
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
                        raise HTTPException(
                            status_code=503,
                            detail="Earth Engine temporarily unavailable due to rate limiting. "
                                   "Please try again in a few seconds.",
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
