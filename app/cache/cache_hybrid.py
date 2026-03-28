"""
Cache híbrido usando Redis para metadados e S3/MinIO para tiles PNG
Otimizado para alta performance e milhões de requisições/segundo
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional, List

import aioboto3
import orjson
import redis.asyncio as redis
from botocore.exceptions import ClientError

from app.core.config import logger, settings, REDIS_URL


class HybridTileCache:
    """
    Cache híbrido de alta performance:
    - Redis/Valkey: Metadados e controle (rápido, pequeno)
    - S3/MinIO: Armazenamento de PNGs (escalável, barato)
    - Cache local em memória: Hot tiles (ultra-rápido)
    """

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

        # Cache local LRU com OrderedDict — evição O(1)
        self.local_cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self.local_cache_size = local_cache_size

        # Configurações de TTL otimizadas
        self.PNG_TTL = 30 * 24 * 3600   # 30 dias para tiles
        self.META_TTL = 7 * 24 * 3600    # 7 dias para metadados
        self.URL_TTL = 24 * 3600         # 24 horas para URLs do Earth Engine
        self.LOCK_TTL = 60               # Lock expira em 60s (safety net)

        # Pool de conexões
        self._redis_pool = None
        self._s3_client = None
        self._s3_ctx = None
        self._s3_lock = asyncio.Lock()
        self._initialized = False
        self.LOCAL_CACHE_TTL = 3600  # 1 hora de TTL no cache local

    def _s3_client_params(self) -> dict:
        """Parâmetros comuns para criação do cliente S3."""
        return {
            "service_name": "s3",
            "endpoint_url": self.s3_endpoint,
            "aws_access_key_id": settings.get("S3_ACCESS_KEY", "minioadmin"),
            "aws_secret_access_key": settings.get("S3_SECRET_KEY", "minioadmin"),
            "use_ssl": settings.get("S3_USE_SSL", True),
            "verify": settings.get("S3_VERIFY_SSL", True),
        }

    async def _ensure_s3_client(self):
        """Garante que o cliente S3 persistente está disponível.

        Usa double-checked locking para evitar race condition em
        reconexões concorrentes.
        """
        if self._s3_client is not None:
            return self._s3_client

        async with self._s3_lock:
            # Double check após adquirir o lock
            if self._s3_client is not None:
                return self._s3_client

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
        """Inicializa conexões e cria bucket se necessário"""
        if self._initialized:
            return

        # Cria pool de conexões Redis
        self._redis_pool = redis.ConnectionPool.from_url(
            self.redis_url,
            max_connections=100,
            decode_responses=False
        )

        # Inicializa cliente S3 persistente e verifica/cria bucket
        try:
            s3 = await self._ensure_s3_client()
            try:
                await s3.head_bucket(Bucket=self.s3_bucket)
            except ClientError:
                await s3.create_bucket(Bucket=self.s3_bucket)
                logger.info(f"Bucket {self.s3_bucket} criado")
            logger.info("S3 conectado com sucesso")
        except Exception as e:
            logger.warning(f"Não foi possível conectar ao S3 ({self.s3_endpoint}): {e}")
            logger.warning("Continuando apenas com cache local e Redis")

        self._initialized = True
        logger.info("HybridTileCache inicializado")

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
        """
        Lock distribuído via Redis para geração de tile.
        Se outro worker já está gerando, espera até o lock ser liberado.
        Retorna True se adquiriu o lock (deve gerar), False se o tile
        ficou disponível enquanto esperava.
        """
        lock_key = f"lock:{key}"
        acquired = False
        try:
            async with self._get_redis() as r:
                acquired = await r.set(lock_key, "1", nx=True, ex=self.LOCK_TTL)

            if acquired:
                yield True
            else:
                for _ in range(self.LOCK_TTL * 2):  # poll a cada 0.5s
                    await asyncio.sleep(0.5)
                    async with self._get_redis() as r:
                        if not await r.exists(lock_key):
                            break
                yield False
        finally:
            if acquired:
                async with self._get_redis() as r:
                    await r.delete(lock_key)

    def _generate_s3_key(self, tile_key: str) -> str:
        """Gera chave S3 com particionamento para melhor performance"""
        hash_prefix = hashlib.md5(tile_key.encode()).hexdigest()[:2]
        return f"tiles/{hash_prefix}/{tile_key}"

    async def get_png(self, key: str) -> Optional[bytes]:
        """
        Busca tile PNG com fallback em cascata:
        1. Cache local (memória)
        2. Redis (metadados) + S3 (dados)
        3. None se não existir
        """
        # 1. Verifica cache local (com TTL)
        if key in self.local_cache:
            data, timestamp = self.local_cache[key]
            if time.time() - timestamp < self.LOCAL_CACHE_TTL:
                self.local_cache.move_to_end(key)
                return data
            else:
                # Expirado — remover para liberar memória
                del self.local_cache[key]

        # 2. Verifica metadados no Redis
        async with self._get_redis() as r:
            meta = await r.hgetall(f"tile:{key}")
            if not meta or not meta.get(b's3_key'):
                return None

            await r.expire(f"tile:{key}", self.META_TTL)

        # 3. Busca do S3
        s3_key = meta[b's3_key'].decode()
        try:
            s3 = await self._ensure_s3_client()
            response = await s3.get_object(Bucket=self.s3_bucket, Key=s3_key)
            data = await response['Body'].read()

            self._update_local_cache(key, data)
            return data
        except ClientError as e:
            logger.error(f"Erro ao buscar {s3_key} do S3: {e}")
            async with self._get_redis() as r:
                await r.delete(f"tile:{key}")
            return None
        except Exception as e:
            logger.error(f"Erro S3 client para {s3_key}: {e}")
            # Cliente pode ter expirado; invalida para reconexão
            self._s3_client = None
            return None

    async def set_png(self, key: str, data: bytes, ttl: int = None) -> None:
        """Salva tile PNG no cache híbrido.

        Best-effort: se o S3 estiver indisponível, o tile é salvo no Redis e
        no cache local para continuar servindo. Nunca propaga exceção.
        """
        if ttl is None:
            ttl = self.PNG_TTL

        self._update_local_cache(key, data)

        s3_key = self._generate_s3_key(key)
        s3_ok = await self._upload_to_s3(s3_key, data)

        try:
            async with self._get_redis() as r:
                meta = {
                    's3_key': s3_key,
                    'size': str(len(data)),
                    'created': datetime.now().isoformat(),
                    'content_type': 'image/png',
                    's3_synced': '1' if s3_ok else '0',
                }
                await r.hset(f"tile:{key}", mapping=meta)
                await r.expire(f"tile:{key}", ttl)
        except Exception as e:
            logger.warning(f"Redis write falhou para tile:{key}: {e}")

    async def _upload_to_s3(self, s3_key: str, data: bytes) -> bool:
        """Upload assíncrono para S3 com retry. Best-effort — nunca propaga exceção."""
        try:
            s3 = await self._ensure_s3_client()
            for attempt in range(3):
                try:
                    await s3.put_object(
                        Bucket=self.s3_bucket,
                        Key=s3_key,
                        Body=data,
                        ContentType='image/png',
                        CacheControl='public, max-age=2592000'
                    )
                    return True
                except Exception as e:
                    if attempt == 2:
                        logger.warning(f"S3 write falhou para {s3_key}: {e}")
                        return False
                    await asyncio.sleep(0.1 * (attempt + 1))
        except Exception as e:
            logger.warning(f"S3 client error para {s3_key}: {e}")
            self._s3_client = None
            return False
        return False

    def _update_local_cache(self, key: str, data: bytes) -> None:
        """Atualiza cache local com LRU eviction O(1) via OrderedDict"""
        if key in self.local_cache:
            self.local_cache.move_to_end(key)
        self.local_cache[key] = (data, time.time())
        if len(self.local_cache) > self.local_cache_size:
            self.local_cache.popitem(last=False)

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
                "hot_tiles": hot_tiles
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
            del self.local_cache[key]
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
        """Fecha conexões ao desligar"""
        if self._s3_client is not None:
            try:
                await self._s3_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._s3_client = None
        if self._redis_pool:
            await self._redis_pool.disconnect()
        self._initialized = False


# Instância global do cache
tile_cache = HybridTileCache(
    redis_url=REDIS_URL,
    s3_endpoint=settings.get("S3_ENDPOINT", "http://minio:9000"),
    s3_bucket=settings.get("S3_BUCKET", "tiles-cache"),
)
