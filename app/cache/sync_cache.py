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


def validate_offline_image(data):
    if not isinstance(data, bytes) or not data:
        raise InvalidTileError('Imagem vazia')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG'} or image.size != (256, 256):
                raise InvalidTileError('Formato ou dimensões de tile inválidos')
            content_type = Image.MIME[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            rgba = image.convert('RGBA')
            if rgba.getchannel('A').getbbox() is None:
                raise InvalidTileError('Imagem totalmente transparente')
            visible = Image.new('RGBA', rgba.size, 'white')
            visible.alpha_composite(rgba)
            if visible.convert('RGB').getextrema() == ((255, 255),) * 3:
                raise InvalidTileError('Imagem totalmente branca')
        return content_type
    except (OSError, SyntaxError, ValueError, UnidentifiedImageError) as exc:
        raise InvalidTileError('Imagem inválida para inspeção offline') from exc


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
            expiry and expiry > time.time() and obj.get('ContentType') in {'image/png', 'image/jpeg'}
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
                if png.format not in {'PNG', 'JPEG'}:
                    raise InvalidTileError('Formato de imagem não suportado')
                content_type = Image.MIME[png.format]
                if png.format == 'JPEG':
                    png.load()
                else:
                    png.verify()
        except (OSError, SyntaxError, ValueError, UnidentifiedImageError) as exc:
            raise InvalidTileError('Conteúdo de imagem inválido') from exc
        lease.ensure_owned()
        expires_at = time.time() + ttl
        object_key = _s3_key(key)
        self.s3.put_object(
            Bucket=self.bucket, Key=object_key, Body=data, ContentType=content_type,
            CacheControl=f'public, max-age={math.ceil(ttl)}',
            Metadata={'expires_at': str(expires_at)},
        )
        lease.ensure_owned()
        self._index_png(key, data, content_type, expires_at, lease)

    def _index_png(self, key, data, content_type, expires_at, lease):
        if lease.key != key:
            raise ValueError('Concessão pertence a outra chave de cache')
        lease.ensure_owned()
        remaining = math.ceil(expires_at - time.time())
        if remaining <= 0:
            raise TimeoutError('Tile expirou durante a persistência')
        with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.hset('tile:' + key, mapping={
                's3_key': _s3_key(key), 'size': str(len(data)),
                'created': datetime.now().isoformat(), 'content_type': content_type,
                's3_synced': '1', 'expires_at': str(expires_at),
                'content_sha256': hashlib.sha256(data).hexdigest(),
            })
            pipeline.expire('tile:' + key, remaining)
            pipeline.execute()

    def read_valid_image(self, key):
        if not self.has_png(key):
            return None
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=_s3_key(key))
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') in {'NoSuchKey', 'NotFound', '404'}:
                return None
            raise
        body = obj['Body']
        try:
            data = body.read(self.max_tile_bytes + 1)
        finally:
            body.close()
        if len(data) > self.max_tile_bytes:
            return None
        try:
            validate_offline_image(data)
            expiry = _expiry(obj.get('Metadata', {}).get('expires_at'))
            index = self.redis.hgetall('tile:' + key)
            index_expiry = _expiry(index.get(b'expires_at'))
            if 'expires_at' not in obj.get('Metadata', {}) and index.get(b's3_synced') == b'1':
                remaining = self.redis.pttl('tile:' + key)
                if remaining > 0:
                    expiry = time.time() + remaining / 1000
            if expiry and index_expiry:
                expiry = min(expiry, index_expiry)
            if expiry is None or expiry <= time.time():
                return None
            return data, expiry
        except InvalidTileError:
            return None

    def has_valid_image(self, key):
        return self.read_valid_image(key) is not None

    def confirm_valid_image(self, key, lease):
        image = self.read_valid_image(key)
        if image is None:
            return False
        data, expires_at = image
        self._index_png(key, data, validate_offline_image(data), expires_at, lease)
        return expires_at


def persist_png_sync(key, data, ttl):
    with SyncTileCache() as cache:
        with cache.tile_lock(key) as lease:
            cache.set_png(key, data, ttl, lease=lease)
