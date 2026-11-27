import io
import time
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
from PIL import Image

from app.cache.sync_cache import SyncTileCache


def storage():
    image = Image.new('RGB', (256, 256), (40, 80, 120))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    data = stream.getvalue()
    cache = SyncTileCache()
    cache.redis = fakeredis.FakeRedis()
    body = io.BytesIO(data)
    response = dict(Body=body, ContentType='image/png', ContentLength=len(data),
                    Metadata={'expires_at': str(time.time() + 3600)})
    cache.s3 = SimpleNamespace(get_object=Mock(return_value=response),
                              head_object=Mock(side_effect=AssertionError('HEAD redundante')))
    return cache, body, data


def test_valid_read_uses_one_get_and_closes_body():
    cache, body, data = storage()
    assert cache.read_valid_object('test')[0] == data
    cache.s3.get_object.assert_called_once()
    cache.s3.head_object.assert_not_called()
    assert body.closed


def test_invalid_index_expiry_is_not_ignored():
    cache, body, _ = storage()
    cache.redis.hset('tile:test', mapping={'expires_at': 'invalid'})
    assert cache.read_valid_object('test') is None
    assert body.closed


def test_expired_object_is_not_returned():
    cache, body, _ = storage()
    cache.s3.get_object.return_value['Metadata']['expires_at'] = str(time.time() - 1)
    assert cache.read_valid_object('test') is None
    assert body.closed


def test_replacement_is_read_instead_of_previous_bytes():
    cache, body, data = storage()
    assert cache.read_valid_object('test')[0] == data
    image = Image.new('RGB', (256, 256), (120, 40, 80))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    replacement = buffer.getvalue()
    cache.s3.get_object.return_value.update(Body=io.BytesIO(replacement), ContentLength=len(replacement))
    assert cache.read_valid_object('test')[0] == replacement


def test_oversized_object_closes_body_without_reading():
    cache, body, _ = storage()
    cache.s3.get_object.return_value['ContentLength'] = cache.max_tile_bytes + 1
    assert cache.read_valid_object('test') is None
    assert body.closed
