from __future__ import annotations

from typing import Awaitable, Callable

from app.utils.http import http_get_bytes


async def fetch_tile_with_rotation(
    *,
    cache_key: str,
    cached_url: str,
    url_factory: Callable[[], Awaitable[str]],
    x: int,
    y: int,
    z: int,
    layer: str,
) -> bytes:
    """Preserva o contrato dos consumidores e a URL existente em limitação de cota.

    Um 429 não comprova expiração do mapa; sua pausa pertence ao orçamento
    compartilhado, sem troca de identidade ou regeneração da imagem.
    """
    return await http_get_bytes(cached_url.format(x=x, y=y, z=z))
