#!/usr/bin/env python3
"""
Dispara pré-aquecimento de cache via Celery workers e monitora progresso em tempo real.

Lê o arquivo JSON de tiles (gerado por generate_tile_urls.py) e enfileira
as tasks no Celery. Monitora o progresso via polling do Redis.

Uso:
    python3 warm_cache.py \
        --tile-urls /tmp/tiles-loadtest-urls.json \
        --redis-url redis://localhost:6379 \
        --max-tiles 500 \
        --monitor
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import datetime

import aiohttp


async def dispatch_warming(base_url: str, tiles: list[dict], batch_size: int = 100) -> str:
    """Envia tiles para a API de warming e retorna o job_id."""
    url = f"{base_url}/api/cache/warm-tiles"

    payload = {
        "tiles": tiles[:batch_size * 100],  # Limitar para evitar payload gigante
        "batch_size": batch_size,
    }

    async with aiohttp.ClientSession() as session:
        # Tenta via API endpoint se existir
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("job_id", "unknown")
            else:
                print(f"API endpoint não disponível (HTTP {resp.status}), usando Celery direto...")
                return None


async def dispatch_warming_celery(tiles: list[dict], broker_url: str):
    """Enfileira tiles diretamente via Celery."""
    # Importação lazy para funcionar fora do container
    from celery import Celery

    app = Celery("tiles", broker=broker_url)

    total = len(tiles)
    print(f"Enfileirando {total} tiles no Celery...")

    tasks_sent = 0
    for tile in tiles:
        params = tile.get("params", {})
        app.send_task(
            "app.tasks.cache_operations.cache_warm_tile",
            kwargs={
                "x": tile["x"],
                "y": tile["y"],
                "z": tile["z"],
                "layer": tile.get("endpoint", "landsat"),
                "period": params.get("period", "MONTH"),
                "year": params.get("year", 2023),
                "month": params.get("month", 8),
                "visparam": params.get("visparam", "landsat-tvi-false"),
                "composite_mode": params.get("compositeMode", "BEST_IMAGE"),
            },
            queue="standard",
        )
        tasks_sent += 1
        if tasks_sent % 100 == 0:
            print(f"  Enfileiradas: {tasks_sent}/{total}")

    print(f"  Total enfileiradas: {tasks_sent}")
    return tasks_sent


async def monitor_cache_progress(redis_url: str, total_expected: int, interval: int = 5):
    """Monitora progresso do cache em tempo real via Redis."""
    import redis.asyncio as redis

    r = redis.from_url(redis_url, decode_responses=False)
    stop = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    start_time = time.time()
    prev_tile_count = 0

    print()
    print("=" * 80)
    print(f" MONITORAMENTO DE CACHE — {datetime.now().strftime('%H:%M:%S')}")
    print(f" Tiles esperados: {total_expected}")
    print("=" * 80)
    print()
    print(f"{'Tempo':>8s} | {'Tiles':>8s} | {'Meta':>8s} | {'Δ/s':>6s} | {'%':>6s} | {'Redis Mem':>10s} | {'Status'}")
    print("-" * 80)

    while not stop.is_set():
        try:
            # Contar chaves
            tile_count = 0
            async for _ in r.scan_iter(match="tile:*", count=1000):
                tile_count += 1

            meta_count = 0
            async for _ in r.scan_iter(match="meta:*", count=1000):
                meta_count += 1

            # Memória
            info = await r.info("memory")
            mem = info.get("used_memory_human", "?")

            # Cálculos
            elapsed = time.time() - start_time
            delta = tile_count - prev_tile_count
            rate = delta / interval if interval > 0 else 0
            pct = (tile_count / total_expected * 100) if total_expected > 0 else 0

            status = "gerando..." if delta > 0 else ("completo" if pct >= 95 else "aguardando")

            elapsed_str = f"{int(elapsed)}s"
            print(
                f"{elapsed_str:>8s} | {tile_count:>8d} | {meta_count:>8d} | "
                f"{rate:>5.1f}/s | {pct:>5.1f}% | {mem:>10s} | {status}"
            )

            prev_tile_count = tile_count

            if pct >= 99.5:
                print()
                print(f"Cache warming completo: {tile_count}/{total_expected} tiles")
                break

        except Exception as e:
            print(f"  Erro no monitor: {e}")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    await r.aclose()

    elapsed = time.time() - start_time
    print()
    print("=" * 80)
    print(f" RESUMO — {elapsed:.0f}s total")
    print(f" Tiles cacheados: {prev_tile_count}")
    print(f" Taxa média: {prev_tile_count / elapsed:.1f} tiles/s")
    print("=" * 80)


async def main(args):
    # Carregar tiles
    with open(args.tile_urls, "r") as f:
        data = json.load(f)

    tiles = data.get("tiles", [])
    total = len(tiles)
    print(f"Carregadas {total} URLs de tiles de {args.tile_urls}")

    if args.max_tiles and args.max_tiles < total:
        tiles = tiles[:args.max_tiles]
        total = len(tiles)
        print(f"Limitado a {total} tiles (--max-tiles {args.max_tiles})")

    # Deduplicar por (x, y, z, endpoint) para evitar trabalho duplicado
    seen = set()
    unique_tiles = []
    for tile in tiles:
        key = (tile["x"], tile["y"], tile["z"], tile.get("endpoint", "landsat"),
               tile.get("params", {}).get("year"), tile.get("params", {}).get("period"))
        if key not in seen:
            seen.add(key)
            unique_tiles.append(tile)
    tiles = unique_tiles
    total = len(tiles)
    print(f"Tiles únicos após deduplicação: {total}")

    # Disparar via Celery direto
    broker_url = args.broker_url
    tasks_sent = await dispatch_warming_celery(tiles, broker_url)

    # Monitorar
    if args.monitor:
        await monitor_cache_progress(args.redis_url, tasks_sent, interval=args.interval)


def cli():
    parser = argparse.ArgumentParser(description="Dispara cache warming via Celery")
    parser.add_argument("--tile-urls", required=True, help="JSON com URLs de tiles")
    parser.add_argument("--redis-url", default="redis://localhost:6379", help="Redis URL para monitoramento")
    parser.add_argument("--broker-url", default="redis://localhost:6379/1", help="Celery broker URL")
    parser.add_argument("--max-tiles", type=int, default=None, help="Limite de tiles a processar")
    parser.add_argument("--interval", type=int, default=5, help="Intervalo de polling (segundos)")
    parser.add_argument("--monitor", action="store_true", default=True, help="Monitorar progresso em tempo real")
    parser.add_argument("--no-monitor", dest="monitor", action="store_false", help="Não monitorar")
    args = parser.parse_args()

    asyncio.run(main(args))


if __name__ == "__main__":
    cli()
