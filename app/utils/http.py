"""Download HTTP com tentativas limitadas e admissão compartilhada."""
from __future__ import annotations

import asyncio
import math

import aiohttp
from fastapi import HTTPException

from app.core.config import logger, settings
from app.core.gee_admission import async_google_request, project_from_url


class EarthEngineRateLimitedError(Exception):
    def __init__(self, message: str, sa_name: str | None = None, retry_after: float = 5):
        super().__init__(message)
        self.sa_name = sa_name
        self.retry_after = retry_after


async def http_get_bytes(
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: float | None = None,
    project: str | None = None,
    background: bool = False,
) -> bytes:
    if timeout is None:
        timeout = float(settings.get('HTTP_GET_BYTES_TIMEOUT', 20.0))
    if not isinstance(max_retries, int) or max_retries < 1:
        raise ValueError('max_retries deve ser um inteiro positivo')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout deve ser positivo e finito')
    if not math.isfinite(base_delay) or base_delay < 0:
        raise ValueError('base_delay deve ser não negativo e finito')
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    budget = project or project_from_url(url)

    for attempt in range(max_retries):
        try:
            async with async_google_request(project=budget, background=background) as lease:
                async with aiohttp.ClientSession(timeout=client_timeout) as session:
                    async with session.get(url, allow_redirects=False) as response:
                        await lease.aresponse(response.status, response.headers.get('Retry-After'))
                        if response.status == 200:
                            return await response.read()
                        if response.status != 429:
                            raise HTTPException(response.status, 'Erro ao buscar recurso no Earth Engine')
            if attempt == max_retries - 1:
                raise EarthEngineRateLimitedError(
                    'Limite de chamadas ao Earth Engine excedido', retry_after=lease.retry_after,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == max_retries - 1:
                raise HTTPException(503, 'Serviço do Earth Engine indisponível') from exc
            delay = base_delay * (2 ** attempt)
            logger.warning('Falha de conexão ao Earth Engine; tentativa {}/{}', attempt + 1, max_retries)
            await asyncio.sleep(delay)

    raise RuntimeError('Download encerrado sem resposta')
