"""Helper de busca de tile do Earth Engine com rotação reativa de SA.

Resolve o caso onde a URL cacheada está vinculada a uma SA penalizada por
429: a rotação isolada do worker não basta, porque o token na URL continua
preso à SA antiga. Este helper orquestra o ciclo completo:

1. Tenta o download com a URL cacheada.
2. Em EarthEngineRateLimitedError:
   a. Rotaciona a SA do worker (acquire SA diferente, libera a antiga).
   b. Invalida o meta-cache da URL (delete_meta).
   c. Chama o `url_factory()` do caller para regenerar a URL via getMapId
      com a nova SA.
   d. Persiste a nova URL no meta-cache (set_meta).
   e. Tenta o download **uma única vez** com a nova URL.
3. Segundo 429 → propaga `EarthEngineRateLimitedError` para o caller
   converter em 503. Não tentamos terceira rodada porque getMapId custa
   ~200–500ms e amplificar custa mais que entregar erro rápido.

Idempotência: o ciclo é seguro em concorrência. Múltiplos workers podem
invalidar a mesma chave; `delete_meta` é no-op em chave ausente. Múltiplas
regenerações simultâneas geram URLs equivalentes — set_meta da última
prevalece, sem perda funcional.

Sobre `tile_lock` na regeneração: a spec mencionou aplicar lock distribuído
ao redor do `url_factory()` para evitar thundering herd de `getMapId`
sob saturação. A implementação atual NÃO usa lock — decisão deliberada
para manter o helper genérico (sem dependência de tile_lock) e porque a
regeneração só ocorre no path de exceção 429, que já é raro. Sob saturação
extrema com N workers atingindo 429 simultaneamente, podem ocorrer N
chamadas redundantes de `getMapId` (200–500ms cada), amplificando a carga
no EE. Se a métrica `gee_tile_url_regen_total` mostrar volume sustentado,
considerar adicionar lock distribuído como follow-up.
"""
from __future__ import annotations

import asyncio
import functools
from datetime import datetime
from typing import Awaitable, Callable

from app.cache.cache import adelete_meta, aset_meta
from app.core.config import logger
from app.core.gee_auth import get_gee_manager
from app.utils.http import EarthEngineRateLimitedError, http_get_bytes


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
    """Faz download do tile, com rotação reativa em caso de 429.

    Args:
        cache_key: Chave do meta-cache da URL (ex: "landsat_MONTH_2007_7_..."/<geohash>).
        cached_url: URL template com placeholders {x}/{y}/{z}, lida do cache.
        url_factory: Async callable que regenera a URL via getMapId. Deve
            ser idempotente do ponto de vista do caller (encapsula geom/dates/vis).
        x, y, z: Coordenadas do tile.
        layer: Nome da camada (usado em métricas e logs).

    Returns:
        Bytes do PNG do tile.

    Raises:
        EarthEngineRateLimitedError: 429 persistente após uma rotação.
        Qualquer outra exceção do `url_factory` ou de `http_get_bytes`.
    """
    try:
        return await http_get_bytes(cached_url.format(x=x, y=y, z=z))
    except EarthEngineRateLimitedError as exc:
        sa_old = exc.sa_name or "<unknown>"

        # Log de detecção do 429 — antes de qualquer ação. Diferenciado do
        # log de rotação efetiva para evitar mentir sobre o que aconteceu.
        logger.warning(
            f"tile_429_detected layer={layer} cache_key={cache_key} "
            f"sa_from={sa_old} reason=tile_429"
        )

        # Invalidar PRIMEIRO. Mesmo que a rotação falhe (saturação total
        # do pool, Redis offline, ee.Initialize quebrado), o próximo request
        # ainda regenera com SA fresca em vez de reusar a URL podre.
        # delete_meta é idempotente — DEL em chave ausente é no-op.
        try:
            await adelete_meta(cache_key)
        except Exception as del_exc:
            logger.warning(f"Falha ao invalidar meta cache {cache_key}: {del_exc}")

        # Tentar rotacionar a SA do worker.
        manager = get_gee_manager()
        if manager is not None:
            # rotate_on_429 é síncrono (segura init_lock + ee.Initialize).
            # Passa trigger="http_429" para diferenciar das rotações REST API
            # nas métricas (gee_sa_rotation_total).
            rotate = functools.partial(manager.rotate_on_429, trigger="http_429")
            try:
                await asyncio.get_running_loop().run_in_executor(None, rotate)
                logger.info(
                    f"sa_rotated_http_429 layer={layer} sa_from={sa_old} "
                    f"sa_to={manager.current_sa_name}"
                )
            except Exception as rot_exc:
                # Rotação falhou — prossegue para regenerar URL na SA atual
                # (que pode ser a mesma). É melhor tentar do que abortar:
                # cache já foi invalidado, e a SA pode ter saído do cooldown
                # entre o 429 e agora.
                logger.warning(
                    f"sa_rotation_failed layer={layer} sa_from={sa_old} "
                    f"error={rot_exc}"
                )

        # Regenerar a URL com a SA nova (via getMapId no url_factory).
        new_url = await url_factory()

        # Persistir a nova URL para próximos requests.
        try:
            await aset_meta(cache_key, {"url": new_url, "date": datetime.now().isoformat()})
        except Exception as set_exc:
            logger.warning(f"Falha ao persistir meta cache {cache_key}: {set_exc}")

        # Métrica: regeneração disparada por 429.
        try:
            from app.core.metrics import gee_tile_url_regen_total
            gee_tile_url_regen_total.labels(layer=layer).inc()
        except Exception:
            pass

        # Retry único — segundo 429 propaga para o caller.
        return await http_get_bytes(new_url.format(x=x, y=y, z=z))
