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


async def http_get_bytes(url: str, *, max_retries: int = 5, base_delay: float = 1.0) -> bytes:
    """Faz download de bytes via HTTP GET com retry e backoff exponencial.

    Trata 429 (rate limit) com backoff exponencial + jitter.
    Retorna os bytes da resposta em caso de 200.
    """
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as sess, sess.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 429:
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
