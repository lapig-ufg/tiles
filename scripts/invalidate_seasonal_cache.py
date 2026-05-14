#!/usr/bin/env python3
"""Invalida cache dos períodos sazonais WET e DRY após mudança de intervalo.

A definição de `_build_periods` foi ampliada (jan-mai ∪ nov-dez para WET; jun-out
com dtEnd exclusivo para DRY), corrigindo também um off-by-one preexistente. Como
o `path_cache` (`{layer}_{period}_{year}_{month}_{visparam}...`) não inclui as
datas, os tiles previamente cacheados refletem o intervalo antigo. Este script
remove essas entradas do Redis (`tile:*_WET_*`, `tile:*_DRY_*`, `meta:*_WET_*`,
`meta:*_DRY_*`) e os objetos correspondentes no S3/MinIO.

Uso::

    # Dry-run (default) — apenas relata o que seria removido
    python scripts/invalidate_seasonal_cache.py

    # Aplicar
    python scripts/invalidate_seasonal_cache.py --apply

    # Apenas WET
    python scripts/invalidate_seasonal_cache.py --apply --periods WET

    # Apenas uma camada
    python scripts/invalidate_seasonal_cache.py --apply --layer landsat
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import AsyncIterator

logger = logging.getLogger("invalidate_seasonal_cache")


def _decode(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="ignore")
    return str(val)


async def iter_keys(redis_client, pattern: str, scan_count: int = 500) -> AsyncIterator[str]:
    async for key in redis_client.scan_iter(match=pattern, count=scan_count):
        yield _decode(key)


async def run(args: argparse.Namespace) -> int:
    import redis.asyncio as redis_async
    import aioboto3

    redis_url = args.redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    s3_endpoint = args.s3_endpoint or os.environ.get("S3_ENDPOINT")
    s3_access = os.environ.get("S3_ACCESS_KEY", "minioadmin")
    s3_secret = os.environ.get("S3_SECRET_KEY", "minioadmin")
    s3_bucket = args.s3_bucket or os.environ.get("S3_BUCKET", "tiles-cache")

    periods = [p.upper() for p in args.periods]
    layers = args.layer if args.layer else ["landsat", "s2_harmonized"]

    patterns_tile = [f"tile:{layer}_{p}_*" for layer in layers for p in periods]
    patterns_meta = [f"meta:{layer}_{p}_*" for layer in layers for p in periods]

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info(
        f"redis={redis_url} s3_endpoint={s3_endpoint} bucket={s3_bucket} "
        f"periods={periods} layers={layers} mode={mode}"
    )
    logger.info(f"tile patterns: {patterns_tile}")
    logger.info(f"meta patterns: {patterns_meta}")

    redis_client = redis_async.from_url(redis_url)
    session = aioboto3.Session()

    tile_count = 0
    meta_count = 0
    s3_deleted = 0
    s3_errors = 0

    async with session.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_access,
        aws_secret_access_key=s3_secret,
    ) as s3_client:
        # --- tiles: precisa ler s3_key antes de deletar ---
        for pattern in patterns_tile:
            async for redis_key in iter_keys(redis_client, pattern, args.scan_count):
                tile_count += 1
                meta_raw = await redis_client.hgetall(redis_key)
                s3_key = None
                for k, v in meta_raw.items():
                    key_str = _decode(k)
                    if key_str == "s3_key":
                        s3_key = _decode(v)
                        break

                if args.verbose:
                    logger.info(f"TILE  redis={redis_key} s3={s3_key}")

                if args.apply:
                    await redis_client.delete(redis_key)
                    if s3_key:
                        try:
                            await s3_client.delete_object(Bucket=s3_bucket, Key=s3_key)
                            s3_deleted += 1
                        except Exception as e:
                            s3_errors += 1
                            logger.warning(f"S3 delete falhou key={s3_key} err={e}")

                if args.limit and tile_count >= args.limit:
                    break
            if args.limit and tile_count >= args.limit:
                break

        # --- meta: apenas DEL no Redis ---
        for pattern in patterns_meta:
            async for redis_key in iter_keys(redis_client, pattern, args.scan_count):
                meta_count += 1
                if args.verbose:
                    logger.info(f"META  redis={redis_key}")
                if args.apply:
                    await redis_client.delete(redis_key)
                if args.limit and meta_count >= args.limit:
                    break
            if args.limit and meta_count >= args.limit:
                break

    await redis_client.aclose()

    logger.info(
        f"tile_keys={tile_count} meta_keys={meta_count} "
        f"s3_deleted={s3_deleted} s3_errors={s3_errors} mode={mode}"
    )
    return 0 if s3_errors == 0 else 2


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="Executar deleção (default: dry-run).")
    p.add_argument("--periods", nargs="+", default=["WET", "DRY"],
                   help="Períodos a invalidar (default: WET DRY).")
    p.add_argument("--layer", nargs="+", default=None,
                   help="Camadas a invalidar (default: landsat s2_harmonized).")
    p.add_argument("--scan-count", type=int, default=500)
    p.add_argument("--limit", type=int, default=None,
                   help="Quantidade máxima de chaves a processar por categoria (útil para testes).")
    p.add_argument("--redis-url", default=None)
    p.add_argument("--s3-endpoint", default=None)
    p.add_argument("--s3-bucket", default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
