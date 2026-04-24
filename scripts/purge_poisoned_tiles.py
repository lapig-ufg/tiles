#!/usr/bin/env python3
"""Remove tiles envenenados (PNG de erro cacheado com size < threshold).

Investigação em produção identificou chaves como
`tile:landsat_WET_2006_4_landsat-tvi-false_BEST_IMAGE/6zp/13/3045_4224.png`
com `size: 334` (PNG placeholder ~300–500 B) cacheadas por 30 dias.

Este script:
1. Varre `SCAN tile:*` no Redis.
2. Para cada chave, lê o `HGETALL` e verifica `size < threshold`.
3. Em `--apply`, deleta a chave Redis e o objeto S3 correspondente.
4. Em `--dry-run` (default) apenas reporta o plano.

Uso::

    # Dry-run (default) com limite padrão de 1024 bytes
    python scripts/purge_poisoned_tiles.py

    # Aplicar
    python scripts/purge_poisoned_tiles.py --apply

    # Apenas pattern específico e primeiro lote
    python scripts/purge_poisoned_tiles.py --pattern 'tile:landsat_*_BEST_IMAGE/*' --limit 10000
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, AsyncIterator

logger = logging.getLogger("purge_poisoned_tiles")


# ------------------------------------------------------------------ puros ---
def _get_either(meta: dict, key: str):
    """Recupera `meta[key]` aceitando chave str ou bytes.

    Usar `get(str) or get(bytes)` é bug: falha para valores falsy legítimos
    (0, "", b"") — exatamente os mais suspeitos em tile envenenado.
    """
    val = meta.get(key)
    if val is None:
        val = meta.get(key.encode("ascii"))
    return val


def is_poisoned(meta: dict, *, threshold_bytes: int) -> bool:
    """Retorna True se o metadado indica tile placeholder/erro.

    Tolerante a size como str/bytes/int. Quando ausente ou não-numérico,
    retorna False (não deletar em caso de dúvida)."""
    raw = _get_either(meta, "size")
    if raw is None:
        return False
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("ascii", errors="ignore")
    try:
        return int(raw) < threshold_bytes
    except (TypeError, ValueError):
        return False


def extract_s3_key(meta: dict) -> str | None:
    raw = _get_either(meta, "s3_key")
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


def _decode_meta(meta_raw: dict) -> dict:
    """Normaliza chaves/valores bytes → str para facilitar leitura."""
    out: dict[str, Any] = {}
    for k, v in meta_raw.items():
        if isinstance(k, (bytes, bytearray)):
            k = k.decode("utf-8", errors="ignore")
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", errors="ignore")
        out[k] = v
    return out


# ------------------------------------------------------------------- I/O ---
async def iter_poisoned(
    redis_client,
    *,
    pattern: str,
    threshold_bytes: int,
    scan_count: int = 500,
    limit: int | None = None,
) -> AsyncIterator[tuple[str, str | None]]:
    """Itera (redis_key, s3_key) de todos os tiles envenenados."""
    yielded = 0
    async for key in redis_client.scan_iter(match=pattern, count=scan_count):
        key_str = key.decode("utf-8", errors="ignore") if isinstance(key, (bytes, bytearray)) else str(key)
        meta_raw = await redis_client.hgetall(key)
        meta = _decode_meta(meta_raw)
        if not is_poisoned(meta, threshold_bytes=threshold_bytes):
            continue
        yield key_str, extract_s3_key(meta)
        yielded += 1
        if limit and yielded >= limit:
            return


async def delete_pair(redis_client, s3_client, bucket: str, redis_key: str, s3_key: str | None):
    await redis_client.delete(redis_key)
    if s3_key:
        try:
            await s3_client.delete_object(Bucket=bucket, Key=s3_key)
        except Exception as e:
            logger.warning(f"S3 delete falhou bucket={bucket} key={s3_key} err={e}")


# ----------------------------------------------------------------- main ----
async def run(args: argparse.Namespace) -> int:
    import redis.asyncio as redis_async
    import aioboto3

    redis_url = args.redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    s3_endpoint = args.s3_endpoint or os.environ.get("S3_ENDPOINT")
    s3_access = os.environ.get("S3_ACCESS_KEY", "minioadmin")
    s3_secret = os.environ.get("S3_SECRET_KEY", "minioadmin")
    s3_bucket = args.s3_bucket or os.environ.get("S3_BUCKET", "cache")

    logger.info(f"redis={redis_url} s3_endpoint={s3_endpoint} bucket={s3_bucket} "
                f"pattern={args.pattern!r} threshold={args.threshold_bytes} "
                f"mode={'APPLY' if args.apply else 'DRY-RUN'}")

    redis_client = redis_async.from_url(redis_url)
    session = aioboto3.Session()

    planned = 0
    deleted = 0
    errors = 0

    async with session.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_access,
        aws_secret_access_key=s3_secret,
    ) as s3_client:
        async for redis_key, s3_key in iter_poisoned(
            redis_client,
            pattern=args.pattern,
            threshold_bytes=args.threshold_bytes,
            scan_count=args.scan_count,
            limit=args.limit,
        ):
            planned += 1
            if args.verbose:
                logger.info(f"POISONED redis={redis_key} s3={s3_key}")
            if args.apply:
                try:
                    await delete_pair(redis_client, s3_client, s3_bucket, redis_key, s3_key)
                    deleted += 1
                except Exception as e:
                    errors += 1
                    logger.exception(f"Delete failed redis={redis_key}: {e}")

    await redis_client.aclose()

    logger.info(f"planned={planned} deleted={deleted} errors={errors} "
                f"mode={'APPLY' if args.apply else 'DRY-RUN'}")
    return 0 if errors == 0 else 2


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="Executar deleção (default: dry-run).")
    p.add_argument("--pattern", default="tile:*",
                   help="Padrão SCAN do Redis (default: tile:*)")
    p.add_argument("--threshold-bytes", type=int, default=1024,
                   help="Tiles com size menor que este valor são considerados envenenados.")
    p.add_argument("--scan-count", type=int, default=500)
    p.add_argument("--limit", type=int, default=None,
                   help="Quantidade máxima de tiles a processar (útil para testes).")
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
