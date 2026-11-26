"""Cache de tiles com índice Redis, persistência S3 e memória limitada."""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional, List

import aioboto3
import orjson
import redis.asyncio as redis
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import logger, settings, REDIS_URL
from app.cache.errors import TileLockUnavailable
from app.cache.metrics import (
    cache_reads_total, cache_uploads_total, cache_pending_uploads,
    cache_pending_bytes, cache_uploads_active, cache_upload_seconds,
    cache_queue_wait_seconds, cache_locks_total, cache_lock_wait_seconds,
)


_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_RENEW_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


class HybridTileCache:

    def __init__(
        self,
        redis_url: str = "redis://valkey:6379",
        s3_endpoint: str = None,
        s3_bucket: str = "tiles-cache",
        local_cache_size: int = 1000,
    ):
        self.redis_url = redis_url
        self.s3_endpoint = s3_endpoint or settings.get("S3_ENDPOINT")
        self.s3_bucket = s3_bucket
        self.s3_session = aioboto3.Session()

        self.local_cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self.local_cache_size = local_cache_size
        self.local_cache_max_bytes = int(settings.get("LOCAL_CACHE_MAX_BYTES", 64 * 1024 * 1024))
        self.local_cache_bytes = 0

        self.PNG_TTL = 30 * 24 * 3600        # 30 dias — tiles do ano corrente
        self.PNG_TTL_HISTORICAL = 365 * 24 * 3600  # 1 ano — rasters imutáveis
        self.META_TTL = 7 * 24 * 3600         # 7 dias para metadados
        self.URL_TTL = 24 * 3600              # 24h — limite imposto pelo Earth Engine
        self.LOCK_TTL = 60                    # Lock expira em 60s (safety net)
        self.LOCK_WAIT_TIMEOUT = float(settings.get("CACHE_LOCK_WAIT_TIMEOUT", 30))
        self.LOCK_POLL_INTERVAL = 0.1

        self._redis_pool = None
        self._s3_client = None
        self._s3_ctx = None
        self._s3_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self.LOCAL_CACHE_TTL = 3600  # 1 hora de TTL no cache local

        # Uploads S3 em background (referência forte evita GC das tasks)
        self._bg_tasks: set[asyncio.Task] = set()
        self.MAX_BG_UPLOADS = int(settings.get("CACHE_MAX_PENDING_UPLOADS", 512))
        self.MAX_PENDING_BYTES = int(settings.get("CACHE_MAX_PENDING_BYTES", 64 * 1024 * 1024))
        self.MAX_TILE_BYTES = int(settings.get("CACHE_MAX_TILE_BYTES", 8 * 1024 * 1024))
        self.UPLOAD_CONCURRENCY = int(settings.get("CACHE_UPLOAD_CONCURRENCY", 8))
        self.IO_TIMEOUT = float(settings.get("CACHE_IO_TIMEOUT", 15))
        self.CLOSE_TIMEOUT = float(settings.get("CACHE_CLOSE_TIMEOUT", 15))
        self.CANCEL_TIMEOUT = float(settings.get("CACHE_CANCEL_TIMEOUT", 1))
        self.RECOVERY_MISS_TTL = 30
        self._pending_uploads: dict[str, asyncio.Task] = {}
        self._pending_since: dict[str, float] = {}
        self.pending_upload_bytes = 0
        self.READ_CONCURRENCY = int(settings.get("CACHE_READ_CONCURRENCY", 16))
        positive = (self.UPLOAD_CONCURRENCY, self.READ_CONCURRENCY, self.MAX_TILE_BYTES,
                    self.IO_TIMEOUT, self.LOCK_WAIT_TIMEOUT)
        nonnegative = (self.local_cache_size, self.local_cache_max_bytes,
                       self.MAX_PENDING_BYTES, self.MAX_BG_UPLOADS, self.CLOSE_TIMEOUT,
                       self.CANCEL_TIMEOUT)
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("Limites de concorrência, tamanho de tile e prazos devem ser positivos")
        if any(not math.isfinite(value) or value < 0 for value in nonnegative):
            raise ValueError("Limites de cache e prazo de fechamento não podem ser negativos")
        self._upload_semaphore = asyncio.Semaphore(self.UPLOAD_CONCURRENCY)
        self._read_semaphore = asyncio.Semaphore(self.READ_CONCURRENCY)
        self._recovery_misses: OrderedDict[str, float] = OrderedDict()

    def _s3_client_params(self) -> dict:
        """Parâmetros comuns para criação do cliente S3."""
        return {
            "service_name": "s3",
            "endpoint_url": self.s3_endpoint,
            "aws_access_key_id": settings.get("S3_ACCESS_KEY", "minioadmin"),
            "aws_secret_access_key": settings.get("S3_SECRET_KEY", "minioadmin"),
            "use_ssl": settings.get("S3_USE_SSL", True),
            "verify": settings.get("S3_VERIFY_SSL", True),
            # A fila tem orçamento limitado; retries internos prolongam sua ocupação.
            "config": BotoConfig(
                connect_timeout=2,
                read_timeout=10,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        }

    async def _ensure_s3_client(self):
        """Garante que o cliente S3 persistente está disponível.

        Usa double-checked locking para evitar race condition em
        reconexões concorrentes.
        """
        if self._s3_client is not None:
            return self._s3_client
        if self._closed:
            raise RuntimeError("Cache encerrado")

        async with self._s3_lock:
            # Double check após adquirir o lock
            if self._s3_client is not None:
                return self._s3_client
            if self._closed:
                raise RuntimeError("Cache encerrado")

            # Limpar contexto anterior se existir
            if self._s3_ctx is not None:
                try:
                    await self._s3_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                self._s3_ctx = None

            ctx = self.s3_session.client(**self._s3_client_params())
            self._s3_client = await ctx.__aenter__()
            self._s3_ctx = ctx

        return self._s3_client

    async def initialize(self):
        """Inicializa conexões e cria bucket se necessário."""
        async with self._init_lock:
            if self._initialized:
                return
            if self._closed:
                raise RuntimeError("Cache encerrado")
            self._redis_pool = redis.ConnectionPool.from_url(
                self.redis_url, max_connections=100, decode_responses=False,
                socket_connect_timeout=2, socket_timeout=2,
            )
            try:
                async with asyncio.timeout(self.IO_TIMEOUT):
                    s3 = await self._ensure_s3_client()
                    try:
                        await s3.head_bucket(Bucket=self.s3_bucket)
                    except ClientError as exc:
                        if exc.response.get('Error', {}).get('Code') not in {'404', 'NoSuchBucket', 'NotFound'}:
                            raise
                        await s3.create_bucket(Bucket=self.s3_bucket)
            except Exception as exc:
                logger.warning("Falha na inicialização do armazenamento de cache: {}", exc)
            self._initialized = True

    async def _auto_init(self):
        """Inicializa automaticamente se necessário."""
        if not self._initialized:
            await self.initialize()

    @asynccontextmanager
    async def _get_redis(self):
        """Context manager para conexão Redis"""
        if self._redis_pool is None:
            await self._auto_init()
        client = redis.Redis(connection_pool=self._redis_pool)
        try:
            yield client
        finally:
            await client.aclose()

    @asynccontextmanager
    async def tile_lock(self, key: str):
        """Admite geração somente com concessão própria; falha com espera limitada."""
        lock_key = f"lock:{key}"
        token = uuid.uuid4().hex
        started = time.monotonic()
        deadline = started + self.LOCK_WAIT_TIMEOUT
        acquired = False
        lease_lost = False
        owner = asyncio.current_task()
        heartbeat = None
        acquisition = None

        async def acquire(timeout):
            async with asyncio.timeout(timeout):
                async with self._get_redis() as r:
                    return await r.set(lock_key, token, nx=True,
                                       px=max(1, int(self.LOCK_TTL * 1000)))

        async def renew():
            nonlocal lease_lost
            try:
                while True:
                    await asyncio.sleep(self.LOCK_TTL / 3)
                    async with asyncio.timeout(self.LOCK_TTL / 3):
                        async with self._get_redis() as r:
                            valid = await r.eval(_RENEW_LOCK, 1, lock_key, token,
                                                 max(1, int(self.LOCK_TTL * 1000)))
                    if not valid:
                        raise TileLockUnavailable("Concessão de geração perdida")
            except asyncio.CancelledError:
                raise
            except Exception:
                lease_lost = True
                cache_locks_total.labels(result="lost").inc()
                owner.cancel()

        try:
            while not acquired:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cache_locks_total.labels(result="timeout").inc()
                    raise TileLockUnavailable("Prazo de espera pelo lock excedido")
                try:
                    acquisition = asyncio.create_task(acquire(min(remaining, self.IO_TIMEOUT)))
                    acquired = await asyncio.shield(acquisition)
                except Exception as exc:
                    cache_locks_total.labels(result="unavailable").inc()
                    raise TileLockUnavailable("Coordenador de cache indisponível") from exc
                if not acquired:
                    await asyncio.sleep(min(self.LOCK_POLL_INTERVAL, remaining))
            cache_lock_wait_seconds.observe(time.monotonic() - started)
            cache_locks_total.labels(result="acquired").inc()
            heartbeat = asyncio.create_task(renew())
            try:
                yield True
            except asyncio.CancelledError as exc:
                if lease_lost:
                    owner.uncancel()
                    raise TileLockUnavailable("Concessão de geração perdida") from exc
                raise
            if lease_lost:
                raise TileLockUnavailable("Concessão de geração perdida")
        finally:
            if acquisition is not None:
                try:
                    # Redis pode aplicar SET antes do cancelamento alcançar o cliente.
                    await asyncio.shield(acquisition)
                except Exception:
                    pass
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if acquisition is not None:
                try:
                    async with asyncio.timeout(self.IO_TIMEOUT):
                        async with self._get_redis() as r:
                            await r.eval(_RELEASE_LOCK, 1, lock_key, token)
                except Exception as exc:
                    logger.warning("Falha na liberação do lock de cache: {}", exc)

    def _generate_s3_key(self, tile_key: str) -> str:
        """Gera chave S3 com particionamento para melhor performance"""
        hash_prefix = hashlib.md5(tile_key.encode()).hexdigest()[:2]
        return f"tiles/{hash_prefix}/{tile_key}"

    @staticmethod
    def _expires_at(value) -> Optional[float]:
        try:
            expiry = float(value)
        except (TypeError, ValueError):
            return None
        return expiry if math.isfinite(expiry) else None

    def _remember_recovery_miss(self, key: str) -> None:
        self._recovery_misses[key] = time.time() + self.RECOVERY_MISS_TTL
        self._recovery_misses.move_to_end(key)
        while len(self._recovery_misses) > max(1, self.local_cache_size):
            self._recovery_misses.popitem(last=False)

    async def get_png(self, key: str) -> Optional[bytes]:
        """Preserva a validade original e recupera índices com validade comprovada."""
        if self._closed:
            return None
        meta = {}
        legacy_expiry = None
        index_available = False
        try:
            async with asyncio.timeout(self.IO_TIMEOUT):
                async with self._get_redis() as r:
                    meta = await r.hgetall(f"tile:{key}")
                    index_available = True
                    if meta and b'expires_at' not in meta:
                        remaining_ms = await r.pttl(f"tile:{key}")
                        if remaining_ms > 0:
                            legacy_expiry = time.time() + remaining_ms / 1000
        except Exception as exc:
            logger.warning("Falha na consulta do índice de cache: {}", exc)
        indexed = bool(meta.get(b's3_key'))
        expiry = self._expires_at(meta.get(b'expires_at')) if indexed else None
        if indexed and b'expires_at' in meta and (expiry is None or expiry <= time.time()):
            self._remove_local(key)
            cache_reads_total.labels(result="expired").inc()
            return None
        if key in self.local_cache:
            data, expires_at = self.local_cache[key]
            digest = meta.get(b'content_sha256')
            if (index_available and time.time() < expires_at
                    and (not digest or digest == hashlib.sha256(data).hexdigest().encode())):
                self.local_cache.move_to_end(key)
                cache_reads_total.labels(result="local").inc()
                return data
            self._remove_local(key)
        if index_available and not indexed and self._recovery_misses.get(key, 0) > time.time():
            cache_reads_total.labels(result="miss").inc()
            return None
        s3_key = meta[b's3_key'].decode() if indexed else self._generate_s3_key(key)
        body = None
        try:
            async with asyncio.timeout(self.IO_TIMEOUT):
                async with self._read_semaphore:
                    s3 = await self._ensure_s3_client()
                    response = await s3.get_object(Bucket=self.s3_bucket, Key=s3_key)
                    body = response['Body']
                    object_meta = response.get('Metadata', {})
                    object_expiry = self._expires_at(object_meta.get('expires_at'))
                    if 'expires_at' in object_meta:
                        expiry = min(expiry, object_expiry) if expiry and object_expiry else object_expiry
                    elif expiry is None:
                        # O índice legado com TTL finito é a única prova de validade disponível.
                        expiry = legacy_expiry
                    if expiry is None or expiry <= time.time():
                        self._remember_recovery_miss(key)
                        cache_reads_total.labels(result="expired").inc()
                        return None
                    content_length = response.get('ContentLength')
                    if content_length is not None and int(content_length) > self.MAX_TILE_BYTES:
                        cache_reads_total.labels(result="oversized").inc()
                        return None
                    chunks = []
                    size = 0
                    while size <= self.MAX_TILE_BYTES:
                        chunk = await body.read(min(65536, self.MAX_TILE_BYTES + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    data = b''.join(chunks)
                    chunks.clear()
                    if content_length is not None and len(data) != int(content_length):
                        cache_reads_total.labels(result="incomplete").inc()
                        return None
                    if len(data) > self.MAX_TILE_BYTES or expiry <= time.time():
                        cache_reads_total.labels(result="expired_or_oversized").inc()
                        return None
            self._update_local_cache(key, data, expiry)
            if not indexed:
                await self._write_index(key, s3_key, len(data), expiry,
                                        content_sha256=hashlib.sha256(data).hexdigest())
            if expiry <= time.time():
                self._remove_local(key)
                cache_reads_total.labels(result="expired").inc()
                return None
            cache_reads_total.labels(result="shared" if indexed else "recovered").inc()
            return data
        except ClientError as exc:
            code = exc.response.get('Error', {}).get('Code')
            if code in {'NoSuchKey', 'NotFound', '404'}:
                self._remember_recovery_miss(key)
                cache_reads_total.labels(result="miss").inc()
            else:
                cache_reads_total.labels(result="error").inc()
                logger.warning("Falha na leitura de tile no S3: {}", exc)
            return None
        except Exception as exc:
            cache_reads_total.labels(result="error").inc()
            logger.warning("Falha na leitura de tile no S3: {}", exc)
            return None
        finally:
            if body is not None:
                body.close()

    async def set_png(self, key: str, data: bytes, ttl: int = None,
                      *, background: bool = False) -> None:
        """Admite persistência limitada e deduplicada; o armazenamento é best-effort."""
        if self._closed:
            cache_uploads_total.labels(result="closed").inc()
            return
        ttl = self.PNG_TTL if ttl is None else ttl
        if not math.isfinite(ttl) or ttl <= 0:
            cache_uploads_total.labels(result="expired").inc()
            return
        pending = self._pending_uploads.get(key)
        if pending is not None:
            cache_uploads_total.labels(result="deduplicated").inc()
            if not background:
                await self._wait_persistence(pending)
            return
        expires_at = time.time() + ttl
        self._update_local_cache(key, data, expires_at)
        if (len(self._pending_uploads) >= self.MAX_BG_UPLOADS
                or self.pending_upload_bytes + len(data) > self.MAX_PENDING_BYTES
                or len(data) > self.MAX_TILE_BYTES):
            cache_uploads_total.labels(result="rejected").inc()
            return
        self._recovery_misses.pop(key, None)
        self.pending_upload_bytes += len(data)
        cache_pending_bytes.inc(len(data))
        cache_pending_uploads.inc()
        cache_uploads_total.labels(result="admitted").inc()
        enqueued_at = time.monotonic()
        task = asyncio.create_task(self._persist_png(key, data, expires_at, enqueued_at))
        self._pending_uploads[key] = task
        self._pending_since[key] = enqueued_at
        self._bg_tasks.add(task)

        def finished(completed):
            self._pending_uploads.pop(key, None)
            self._pending_since.pop(key, None)
            self._bg_tasks.discard(completed)
            self.pending_upload_bytes -= len(data)
            cache_pending_bytes.dec(len(data))
            cache_pending_uploads.dec()
            result = "cancelled" if completed.cancelled() else completed.result()
            cache_uploads_total.labels(result=result).inc()
            cache_upload_seconds.labels(result=result).observe(time.monotonic() - enqueued_at)

        task.add_done_callback(finished)
        if not background:
            await self._wait_persistence(task)

    @staticmethod
    async def _wait_persistence(task):
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return task.result()

    async def _write_index(self, key: str, s3_key: str, size: int, expires_at: float,
                           *, content_sha256: str = '') -> bool:
        remaining = math.ceil(expires_at - time.time())
        if remaining <= 0:
            return False
        try:
            async with asyncio.timeout(self.IO_TIMEOUT):
                async with self._get_redis() as r:
                    await r.hset(f"tile:{key}", mapping={
                        's3_key': s3_key, 'size': str(size),
                        'created': datetime.now().isoformat(),
                        'content_type': 'image/png', 's3_synced': '1',
                        'expires_at': str(expires_at), 'content_sha256': content_sha256,
                    })
                    await r.expire(f"tile:{key}", remaining)
            return True
        except Exception as exc:
            logger.warning("Falha na escrita do índice de cache: {}", exc)
            return False

    async def _persist_png(self, key: str, data: bytes, expires_at: float,
                           enqueued_at: float) -> str:
        outcome = "failed"
        try:
            async with asyncio.timeout(self.IO_TIMEOUT):
                async with self._upload_semaphore:
                    cache_queue_wait_seconds.observe(time.monotonic() - enqueued_at)
                    if expires_at <= time.time():
                        return "expired"
                    cache_uploads_active.inc()
                    try:
                        uploaded = await self._upload_to_s3(self._generate_s3_key(key), data,
                                                            expires_at=expires_at)
                        if uploaded:
                            indexed = await self._write_index(key, self._generate_s3_key(key),
                                                              len(data), expires_at,
                                                              content_sha256=hashlib.sha256(data).hexdigest())
                            outcome = "persisted" if indexed else "persisted_unindexed"
                    finally:
                        cache_uploads_active.dec()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Falha na persistência de tile: {}", exc)
        return outcome

    async def _upload_to_s3(self, s3_key: str, data: bytes, *, expires_at: float) -> bool:
        s3 = await self._ensure_s3_client()
        await s3.put_object(
            Bucket=self.s3_bucket, Key=s3_key, Body=data, ContentType='image/png',
            CacheControl=f'public, max-age={max(0, math.ceil(expires_at - time.time()))}',
            Metadata={'expires_at': str(expires_at)},
        )
        return True

    def _remove_local(self, key: str) -> None:
        item = self.local_cache.pop(key, None)
        if item is not None:
            self.local_cache_bytes -= len(item[0])

    def _update_local_cache(self, key: str, data: bytes, expires_at: float = None) -> None:
        self._remove_local(key)
        if len(data) > self.local_cache_max_bytes or self.local_cache_size <= 0:
            return
        expiry = min(time.time() + self.LOCAL_CACHE_TTL,
                     expires_at if expires_at is not None else float('inf'))
        self.local_cache[key] = (data, expiry)
        self.local_cache_bytes += len(data)
        while (len(self.local_cache) > self.local_cache_size
               or self.local_cache_bytes > self.local_cache_max_bytes):
            _, (removed, _) = self.local_cache.popitem(last=False)
            self.local_cache_bytes -= len(removed)

    async def get_meta(self, key: str) -> Optional[Dict[str, Any]]:
        """Busca metadados (URLs do Earth Engine, etc)"""
        async with self._get_redis() as r:
            data = await r.get(f"meta:{key}")
            if data:
                await r.expire(f"meta:{key}", self.URL_TTL)
                return orjson.loads(data)
        return None

    async def set_meta(self, key: str, meta: Dict[str, Any], ttl: int = None) -> None:
        """Salva metadados no Redis"""
        if ttl is None:
            ttl = self.URL_TTL

        async with self._get_redis() as r:
            await r.set(f"meta:{key}", orjson.dumps(meta), ex=ttl)

    async def delete_meta(self, key: str) -> None:
        """Remove metadados (URL EE etc.) do Redis. Usado para invalidar
        URLs cacheadas vinculadas a SAs penalizadas por 429.

        Idempotente — DEL em chave inexistente é no-op no Redis.
        """
        async with self._get_redis() as r:
            await r.delete(f"meta:{key}")

    async def batch_get_tiles(self, keys: List[str]) -> Dict[str, Optional[bytes]]:
        """Busca múltiplos tiles em paralelo para melhor performance"""
        tasks = [self.get_png(key) for key in keys]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            key: result if not isinstance(result, Exception) else None
            for key, result in zip(keys, results)
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do cache para monitoramento"""
        async with self._get_redis() as r:
            info = await r.info()
            dbsize = await r.dbsize()

        s3_stats = await self._get_s3_stats()

        # Hot tiles: últimos 10 itens do OrderedDict (mais recentemente acessados)
        hot_tiles = list(self.local_cache.keys())[-10:]

        return {
            "redis": {
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_human": info.get("used_memory_human", "0"),
                "total_keys": dbsize,
            },
            "local_cache": {
                "size": len(self.local_cache),
                "max_size": self.local_cache_size,
                "bytes": self.local_cache_bytes,
                "max_bytes": self.local_cache_max_bytes,
                "hot_tiles": hot_tiles
            },
            "uploads": {
                "pending": len(self._pending_uploads),
                "pending_bytes": self.pending_upload_bytes,
                "max_pending_bytes": self.MAX_PENDING_BYTES,
                "oldest_age_seconds": max(
                    (time.monotonic() - started for started in self._pending_since.values()),
                    default=0,
                ),
            },
            "s3": s3_stats
        }

    async def _get_s3_stats(self) -> Dict[str, Any]:
        """Coleta estatísticas gerais do bucket S3/MinIO"""
        try:
            s3 = await self._ensure_s3_client()
            await s3.head_bucket(Bucket=self.s3_bucket)

            response = await s3.list_objects_v2(Bucket=self.s3_bucket, MaxKeys=1)

            if 'Contents' in response:
                sample_response = await s3.list_objects_v2(Bucket=self.s3_bucket, MaxKeys=1000)

                sample_objects = len(sample_response.get('Contents', []))
                sample_size = sum(obj.get('Size', 0) for obj in sample_response.get('Contents', []))

                if sample_response.get('IsTruncated', False):
                    estimated_total_objects = sample_objects * 1000
                    estimated_total_size = sample_size * 1000
                else:
                    estimated_total_objects = sample_objects
                    estimated_total_size = sample_size

                return {
                    "connected": True,
                    "total_objects": estimated_total_objects,
                    "size_bytes": estimated_total_size,
                    "size_mb": round(estimated_total_size / (1024 * 1024), 2),
                    "size_gb": round(estimated_total_size / (1024 * 1024 * 1024), 2),
                    "bucket": self.s3_bucket,
                    "endpoint": self.s3_endpoint,
                    "avg_object_size_kb": round((estimated_total_size / 1024) / max(estimated_total_objects, 1), 2),
                    "estimation_method": "sample_based" if sample_response.get('IsTruncated', False) else "complete"
                }
            else:
                return {
                    "connected": True,
                    "total_objects": 0,
                    "size_bytes": 0,
                    "size_mb": 0,
                    "size_gb": 0,
                    "bucket": self.s3_bucket,
                    "endpoint": self.s3_endpoint,
                    "avg_object_size_kb": 0,
                    "estimation_method": "complete"
                }

        except Exception as e:
            logger.error(f"Erro ao obter estatísticas do S3: {e}")
            return {
                "connected": False,
                "error": str(e),
                "total_objects": 0,
                "size_bytes": 0,
                "size_mb": 0,
                "size_gb": 0,
                "bucket": self.s3_bucket,
                "endpoint": self.s3_endpoint,
                "estimation_method": "failed"
            }

    async def delete_by_pattern(self, pattern: str) -> int:
        """
        Remove entradas do cache que correspondem ao padrão.
        Retorna o número de itens removidos.
        """
        deleted_count = 0

        async with self._get_redis() as r:
            tile_keys = []
            async for key in r.scan_iter(match=f"tile:{pattern}*"):
                tile_keys.append(key)

            meta_keys = []
            async for key in r.scan_iter(match=f"meta:{pattern}*"):
                meta_keys.append(key)

            if tile_keys or meta_keys:
                s3_keys_to_delete = []
                for tile_key in tile_keys:
                    meta = await r.hgetall(tile_key)
                    if meta and meta.get(b's3_key'):
                        s3_keys_to_delete.append(meta[b's3_key'].decode())

                if tile_keys:
                    await r.delete(*tile_keys)
                    deleted_count += len(tile_keys)
                if meta_keys:
                    await r.delete(*meta_keys)
                    deleted_count += len(meta_keys)

                if s3_keys_to_delete:
                    await self._batch_delete_from_s3(s3_keys_to_delete)

        # Remove do cache local
        keys_to_remove = [k for k in self.local_cache.keys() if k.startswith(pattern)]
        for key in keys_to_remove:
            self._remove_local(key)
            deleted_count += 1

        logger.info(f"Removidos {deleted_count} itens do cache com padrão: {pattern}")
        return deleted_count

    async def _batch_delete_from_s3(self, s3_keys: List[str]) -> None:
        """Remove múltiplos objetos do S3 em batch"""
        if not s3_keys:
            return

        try:
            s3 = await self._ensure_s3_client()
            for i in range(0, len(s3_keys), 1000):
                batch = s3_keys[i:i+1000]
                objects = [{'Key': key} for key in batch]

                try:
                    await s3.delete_objects(
                        Bucket=self.s3_bucket,
                        Delete={'Objects': objects}
                    )
                    logger.info(f"Removidos {len(batch)} objetos do S3")
                except Exception as e:
                    logger.error(f"Erro ao remover objetos do S3: {e}")
        except Exception as e:
            logger.error(f"Erro S3 client em batch delete: {e}")
            self._s3_client = None

    async def clear_cache_by_layer(self, layer: str) -> int:
        """Remove todo o cache de uma camada específica"""
        return await self.delete_by_pattern(f"{layer}_")

    async def clear_cache_by_year(self, year: int) -> int:
        """Remove todo o cache de um ano específico"""
        return await self.delete_by_pattern(f"*_{year}_")

    async def clear_cache_by_point(self, x: int, y: int, z: int) -> int:
        """Remove cache de um tile específico"""
        return await self.delete_by_pattern(f"*/{z}/{x}_{y}")

    async def close(self):
        """Interrompe novas admissões e drena uploads antes de fechar os clientes."""
        self._closed = True
        pending = [task for task in self._bg_tasks if not task.done()]
        try:
            if pending:
                await asyncio.wait(pending, timeout=self.CLOSE_TIMEOUT)
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                _, unfinished = await asyncio.wait(pending, timeout=self.CANCEL_TIMEOUT)
                if unfinished:
                    cache_uploads_total.labels(result="shutdown_pending").inc(len(unfinished))
                    logger.warning("Uploads aguardando cancelamento após o encerramento: {}",
                                   len(unfinished))
            try:
                if self._s3_ctx is not None:
                    async with asyncio.timeout(self.IO_TIMEOUT):
                        await self._s3_ctx.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("Falha no fechamento do cliente S3: {}", exc)
            try:
                if self._redis_pool is not None:
                    async with asyncio.timeout(self.IO_TIMEOUT):
                        await self._redis_pool.disconnect()
            except Exception as exc:
                logger.warning("Falha no fechamento do pool Redis: {}", exc)
            finally:
                self._s3_ctx = None
                self._s3_client = None
                self._redis_pool = None
                self._initialized = False
                self.local_cache.clear()
                self.local_cache_bytes = 0
                self._recovery_misses.clear()


# Instância global do cache
tile_cache = HybridTileCache(
    redis_url=REDIS_URL,
    s3_endpoint=settings.get("S3_ENDPOINT", "http://minio:9000"),
    s3_bucket=settings.get("S3_BUCKET", "tiles-cache"),
    local_cache_size=int(settings.get("LOCAL_CACHE_SIZE", 1000)),
)
