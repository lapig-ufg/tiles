import hashlib
import io
import math
import time
from contextlib import ExitStack
from datetime import datetime

import boto3
import redis
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

from app.cache.sync_lock import sync_tile_lock
from app.core.config import settings


class InvalidTileError(ValueError):
    pass


def _expiry(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _s3_key(key):
    return f'tiles/{hashlib.md5(key.encode()).hexdigest()[:2]}/{key}'


class SyncTileCache:
    def __init__(self):
        self.max_tile_bytes = int(settings.get('CACHE_MAX_TILE_BYTES', 8 * 1024 * 1024))
        self.io_timeout = float(settings.get('CACHE_IO_TIMEOUT', 15))
        self.lock_wait = float(settings.get('CACHE_LOCK_WAIT_TIMEOUT', 30))
        if any(not math.isfinite(value) or value <= 0 for value in
               (self.max_tile_bytes, self.io_timeout, self.lock_wait)):
            raise ValueError('Limites de cache devem ser positivos e finitos')
        self.bucket = settings.get('S3_BUCKET', 'tiles-cache')
        self._resources = ExitStack()

    def __enter__(self):
        try:
            self.redis = redis.Redis.from_url(
                settings.get('REDIS_URL', 'redis://valkey:6379'), decode_responses=False,
                socket_connect_timeout=min(3, self.io_timeout),
                socket_timeout=min(3, self.io_timeout), retry_on_timeout=False,
            )
            self._resources.callback(self.redis.close)
            self.s3 = boto3.client(
                's3', endpoint_url=settings.get('S3_ENDPOINT', 'http://minio:9000'),
                aws_access_key_id=settings.get('S3_ACCESS_KEY', 'minioadmin'),
                aws_secret_access_key=settings.get('S3_SECRET_KEY', 'minioadmin'),
                use_ssl=settings.get('S3_USE_SSL', True),
                verify=settings.get('S3_VERIFY_SSL', True),
                config=Config(connect_timeout=min(3, self.io_timeout), read_timeout=self.io_timeout,
                              retries={'total_max_attempts': 1, 'mode': 'standard'}),
            )
            self._resources.callback(self.s3.close)
            return self
        except BaseException:
            self._resources.close()
            raise

    def __exit__(self, *args):
        return self._resources.__exit__(*args)

    def tile_lock(self, key):
        return sync_tile_lock(self.redis, key, wait_timeout=self.lock_wait)

    def has_png(self, key):
        index = self.redis.hgetall('tile:' + key)
        index_expiry = _expiry(index.get(b'expires_at'))
        if b'expires_at' in index and (index_expiry is None or index_expiry <= time.time()):
            return False
        try:
            obj = self.s3.head_object(Bucket=self.bucket, Key=_s3_key(key))
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') in {'NoSuchKey', 'NotFound', '404'}:
                return False
            raise
        metadata = obj.get('Metadata', {})
        expiry = _expiry(metadata.get('expires_at'))
        if 'expires_at' not in metadata and index.get(b's3_synced') == b'1':
            remaining = self.redis.pttl('tile:' + key)
            if remaining > 0:
                expiry = time.time() + remaining / 1000
        if index_expiry and expiry:
            expiry = min(index_expiry, expiry)
        return bool(
            expiry and expiry > time.time() and obj.get('ContentType') == 'image/png'
            and 0 < int(obj.get('ContentLength', 0)) <= self.max_tile_bytes
        )

    def set_png(self, key, data, ttl, *, lease):
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError('TTL deve ser positivo e finito')
        if lease.key != key:
            raise ValueError('Concessão pertence a outra chave de cache')
        if not isinstance(data, bytes) or not data or len(data) > self.max_tile_bytes:
            raise InvalidTileError('Tamanho de tile inválido')
        try:
            with Image.open(io.BytesIO(data)) as png:
                if png.format != 'PNG':
                    raise InvalidTileError('Conteúdo não é PNG')
                png.verify()
        except (OSError, SyntaxError, ValueError, UnidentifiedImageError) as exc:
            raise InvalidTileError('Conteúdo PNG inválido') from exc
        lease.ensure_owned()
        expires_at = time.time() + ttl
        object_key = _s3_key(key)
        self.s3.put_object(
            Bucket=self.bucket, Key=object_key, Body=data, ContentType='image/png',
            CacheControl=f'public, max-age={math.ceil(ttl)}',
            Metadata={'expires_at': str(expires_at)},
        )
        lease.ensure_owned()
        remaining = math.ceil(expires_at - time.time())
        if remaining <= 0:
            raise TimeoutError('Tile expirou durante a persistência')
        with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.hset('tile:' + key, mapping={
                's3_key': object_key, 'size': str(len(data)),
                'created': datetime.now().isoformat(), 'content_type': 'image/png',
                's3_synced': '1', 'expires_at': str(expires_at),
            })
            pipeline.expire('tile:' + key, remaining)
            pipeline.execute()


def persist_png_sync(key, data, ttl):
    with SyncTileCache() as cache:
        with cache.tile_lock(key) as lease:
            cache.set_png(key, data, ttl, lease=lease)
