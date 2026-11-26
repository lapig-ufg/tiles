import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import fakeredis
import numpy as np
import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Storage:
    def __init__(self, root):
        self.root = root
        self.metadata = {}
        self.uploads = 0

    def head_object(self, Bucket, Key):
        if Key not in self.metadata:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        return dict(ContentType='image/png', ContentLength=self.path(Key).stat().st_size,
                    Metadata=self.metadata[Key])

    def path(self, key):
        return self.root / key.replace('/', '_')

    def put_object(self, **kwargs):
        key = kwargs['Key']
        self.path(key).write_bytes(kwargs['Body'])
        self.metadata[key] = kwargs['Metadata']
        self.uploads += 1

    def get_object(self, Bucket, Key):
        return dict(Body=self.path(Key).open('rb'), Metadata=self.metadata[Key])


@pytest.fixture
def state(tmp_path, monkeypatch):
    from app.cache.sync_cache import SyncTileCache
    from app.modules.bdc import offline_tiles
    from app.modules.bdc.service import BdcService
    storage = Storage(tmp_path)
    client = fakeredis.FakeRedis()
    cache = SyncTileCache()
    cache.redis, cache.s3 = client, storage
    @contextmanager
    def factory():
        yield cache
    monkeypatch.setattr(offline_tiles, 'SyncTileCache', factory)
    service = BdcService(tmp_path / 'bdc')
    calls = []
    monkeypatch.setattr(service, 'detail', lambda *args: calls.append('detail') or {
        'item': {'id': 'scene'}, 'profiles': [{'id': 'rgb'}]})
    @contextmanager
    def remote():
        calls.append('remote')
        yield SimpleNamespace(events=[])
    monkeypatch.setattr(service, 'remote', remote)
    pixels = np.full((256, 256, 4), [90, 120, 80, 255], dtype=np.uint8)
    monkeypatch.setattr(service, 'render_pixels', lambda *args: pixels)
    return SimpleNamespace(storage=storage, cache=cache, service=service, calls=calls, pixels=pixels, offline_tiles=offline_tiles)


def warm(state, **kwargs):
    return state.offline_tiles.warm_tile(state.service, 'landsat-2', 'scene', 'rgb', 6, 0, 0, **kwargs)


def test_durable_render_survives_local_cache_and_origin_loss(state, monkeypatch):
    from app.modules.bdc.router import create_router
    from app.modules.bdc.service import BdcService, encode_image
    result = warm(state)
    assert result['status'] == 'cached'
    def no_origin(*args):
        pytest.fail('O cache durável não deve consultar a origem')
    service = BdcService(state.service.root / 'another-replica')
    monkeypatch.setattr(service, 'detail', no_origin)
    data, stats = service.render('landsat-2', 'scene', 6, 0, 0, 'rgb', 'png')
    assert data == encode_image(state.pixels, 'png')
    assert stats['cache'] == 'HIT' and stats['expires_at'] == result['expires_at']
    app = FastAPI()
    app.include_router(create_router(service), prefix='/api/bdc')
    with TestClient(app) as client:
        response = client.get('/api/bdc/tiles/landsat-2/scene/6/0/0.png?profile=rgb')
    assert response.status_code == 200
    assert response.content == data
    assert response.headers['X-Range-Requests'] == '0'
    assert warm(state)['status'] == 'already_cached'
    assert state.storage.uploads == 1


@pytest.mark.parametrize('damage', ['corrupt', 'expired', 'transparent'])
def test_invalid_storage_is_never_a_hit(state, damage):
    from app.modules.bdc.service import encode_image
    warm(state)
    key = next(iter(state.storage.metadata))
    if damage == 'corrupt':
        state.storage.path(key).write_bytes(b'not a PNG')
    elif damage == 'expired':
        state.storage.metadata[key]['expires_at'] = str(time.time() - 1)
    else:
        state.storage.path(key).write_bytes(encode_image(np.zeros((256, 256, 4), dtype=np.uint8), 'png'))
    assert state.offline_tiles.read_tile('landsat-2', 'scene', 'rgb', 6, 0, 0) is None


def test_same_tile_workers_generate_once(state):
    barrier = threading.Barrier(2)
    def operation():
        barrier.wait()
        return warm(state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: operation(), range(2)))
    assert sorted(row['status'] for row in results) == ['already_cached', 'cached']
    assert state.calls.count('detail') == 1
    assert state.storage.uploads == 1


def test_invalid_pixels_do_not_confirm_offline_success(state):
    state.pixels[:] = 0
    result = warm(state)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert state.storage.uploads == 0


def test_force_uses_same_scene_and_regenerates(state):
    first = warm(state)
    result = warm(state, force=True)
    assert result['status'] == 'cached'
    assert result['key'] == first['key']
    assert state.calls.count('detail') == 2
    assert state.storage.uploads == 2


def test_upload_failure_is_not_success(state, monkeypatch):
    def failure(**kwargs):
        raise IOError('Storage indisponível')
    monkeypatch.setattr(state.storage, 'put_object', failure)
    with pytest.raises(IOError):
        warm(state)
    assert state.offline_tiles.read_tile('landsat-2', 'scene', 'rgb', 6, 0, 0) is None


def test_upload_requires_readback_confirmation(state, monkeypatch):
    monkeypatch.setattr(state.storage, 'put_object', lambda **kwargs: None)
    with pytest.raises(IOError, match='não confirmada'):
        warm(state)



def test_visualization_change_invalidates_durable_key(state, monkeypatch):
    first = warm(state)
    revision = state.offline_tiles.renderer_revision()
    visualizations = state.offline_tiles.VIS
    bands, minimum, maximum, gamma = visualizations['landsat-tvi-true']
    monkeypatch.setitem(visualizations, 'landsat-tvi-true', (bands, minimum, maximum, gamma + .1))
    assert state.offline_tiles.renderer_revision() != revision
    key = state.offline_tiles.tile_key('landsat-2', 'scene', 'rgb', 6, 0, 0)
    assert key != first['key']
    assert state.offline_tiles.read_tile('landsat-2', 'scene', 'rgb', 6, 0, 0) is None


def test_proposed_configuration_changes_renderer_revision(state, tmp_path, monkeypatch):
    module = state.offline_tiles
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'offline_tiles.py'))
    absent = module.renderer_revision()
    proposed = tmp_path / 'proposed-visparams.json'
    proposed.write_text('{"rgb": {"min": [0], "max": [1]}}')
    configured = module.renderer_revision()
    assert configured != absent
    proposed.write_text('{"rgb": {"min": [0], "max": [2]}}')
    assert module.renderer_revision() != configured


@pytest.mark.parametrize('change', ['version', 'palette'])
def test_renderer_change_replaces_local_pixels_before_durable_upload(state, monkeypatch, change):
    from app.modules.bdc.service import encode_image
    first = warm(state)
    original = state.offline_tiles.read_tile('landsat-2', 'scene', 'rgb', 6, 0, 0)[0]
    if change == 'version':
        monkeypatch.setattr(state.offline_tiles, 'RENDER_VERSION', state.offline_tiles.RENDER_VERSION + 1)
    else:
        monkeypatch.setattr(state.offline_tiles, 'PALETTE', ['000000', 'ffffff'])
    state.pixels[:] = [150, 60, 80, 255]
    second = warm(state)
    actual = state.offline_tiles.read_tile('landsat-2', 'scene', 'rgb', 6, 0, 0)[0]
    assert second['key'] != first['key']
    assert actual != original
    assert actual == encode_image(state.pixels, 'png')
    assert state.calls.count('remote') == 2
    assert state.storage.uploads == 2
