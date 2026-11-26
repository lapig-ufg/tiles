import asyncio
import importlib
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from email.message import Message
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError

import fakeredis
import pytest
from aiohttp import web


@pytest.fixture
def tasks(monkeypatch):
    module = importlib.import_module('app.tasks.tile_tasks')
    auth = importlib.import_module('app.core.gee_auth')
    monkeypatch.setattr(auth, 'initialize_earth_engine', lambda: None)
    monkeypatch.setattr(auth, 'get_gee_manager', lambda: None)
    return module


@pytest.fixture
def coordinator(monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    controller = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True),
        admission.AdmissionLimits(wait_seconds=0),
    )
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: controller)
    monkeypatch.setattr(admission, '_sdk_transport_installed', False)
    return controller


class DownloadResponse(io.BytesIO):
    status = 200
    headers = {}


@pytest.mark.parametrize('layer', ['landsat', 'sentinel2'])
def test_get_map_id_reserves_background_capacity(tasks, coordinator, monkeypatch, layer):
    calls = []

    def get_map_id(parameters):
        calls.append(parameters)
        assert coordinator.redis.zcard('gee:{admission}:shared:active_bg') == 1
        return {'tile_fetcher': SimpleNamespace(url_format='https://example.test/{z}/{x}/{y}')}

    monkeypatch.setattr(tasks.ee.data, 'getMapId', get_map_id)
    parameters = {'year': 2020, 'vis_param': 'landsat-tvi-false' if layer == 'landsat' else 'tvi-red'}
    bounds = {'west': -50, 'east': -49, 'north': -10, 'south': -11}
    generate = tasks._generate_landsat_tile if layer == 'landsat' else tasks._generate_sentinel2_tile
    assert generate(bounds, parameters) == 'https://example.test/{z}/{x}/{y}'
    assert len(calls) == 1
    assert 'image' in calls[0]
    assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0


def test_landsat_does_not_mutate_shared_visualization_parameters(tasks, coordinator, monkeypatch):
    visualization = {'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                     'max': [0.6, 0.55, 0.3], 'gamma': [1.2]}
    monkeypatch.setattr(tasks, 'get_landsat_vis_params', lambda *_: visualization)
    sent = []
    def get_map_id(parameters):
        sent.append(parameters)
        return {'tile_fetcher': SimpleNamespace(url_format='https://example.test/tile')}
    monkeypatch.setattr(tasks.ee.data, 'getMapId', get_map_id)
    tasks._generate_landsat_tile({'west': -50, 'east': -49, 'north': -10, 'south': -11},
                                {'year': 2020, 'vis_param': 'landsat-tvi-false'})
    assert visualization['min'] == [0.05, 0.05, 0.03]
    assert visualization['gamma'] == [1.2]
    assert sent[0]['min'] == '0.05,0.05,0.03'


@pytest.mark.parametrize('path', ['single', 'task'])
def test_sync_download_is_admitted_as_background(tasks, coordinator, monkeypatch, path):
    monkeypatch.setattr(tasks, '_generate_sentinel2_tile', lambda *_: 'https://earthengine.googleapis.com/v1/projects/test-project/tiles/{z}/{x}/{y}')
    monkeypatch.setattr(tasks, '_save_tile_to_redis', lambda *_: None)
    calls = []

    def download(self, request, data=None, timeout=None):
        calls.append((request.full_url, timeout))
        assert coordinator.redis.zcard('gee:{admission}:shared:active_bg') == 1
        return DownloadResponse(b'PNG')

    monkeypatch.setattr(tasks.urllib.request.OpenerDirector, 'open', download)
    if path == 'single':
        result = tasks._process_single_tile(12, 34, 10, 'sentinel2', {'year': 2020})
    else:
        result = tasks.tile_generate.run(10, 12, 34, 'sentinel2', {'year': 2020})
    assert result['status'] == 'success'
    assert calls == [('https://earthengine.googleapis.com/v1/projects/test-project/tiles/10/12/34', 60)]
    assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0


def test_sync_http_429_shares_cooldown_and_closes_response(tasks, coordinator, monkeypatch):
    monkeypatch.setattr(tasks, '_generate_sentinel2_tile', lambda *_: 'https://earthengine.googleapis.com/v1/projects/test-project/tiles/{z}/{x}/{y}')
    headers = Message()
    headers['Retry-After'] = '30'
    response_body = io.BytesIO(b'quota')
    calls = []

    def download(self, request, data=None, timeout=None):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 429, 'Too Many Requests', headers, response_body)

    monkeypatch.setattr(tasks.urllib.request.OpenerDirector, 'open', download)
    first = tasks._process_single_tile(12, 34, 10, 'sentinel2', {'year': 2020})
    second = tasks._process_single_tile(13, 34, 10, 'sentinel2', {'year': 2020})
    assert first['status'] == second['status'] == 'error'
    assert len(calls) == 1
    assert response_body.closed
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0


@asynccontextmanager
async def local_endpoint(port):
    app = web.Application()
    async def handler(request):
        return web.Response(body=b'PNG')
    app.router.add_get('/{z}/{x}/{y}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', port).start()
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_async_download_debits_background_budget(tasks, coordinator, unused_tcp_port):
    async with local_endpoint(unused_tcp_port) as base:
        assert await tasks._download_tile(base + '/{z}/{x}/{y}', 12, 34, 10) == b'PNG'
    assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0


@pytest.mark.asyncio
async def test_sync_redirect_does_not_send_an_unaccounted_request(tasks, coordinator, unused_tcp_port):
    calls = []
    async def redirect(request):
        calls.append('redirect')
        raise web.HTTPFound('/target')
    async def target(request):
        calls.append('target')
        return web.Response(body=b'PNG')
    app = web.Application()
    app.router.add_get('/redirect', redirect)
    app.router.add_get('/target', target)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', unused_tcp_port).start()
    try:
        with pytest.raises(HTTPError) as error:
            await asyncio.to_thread(tasks._download_tile_sync, f'http://127.0.0.1:{unused_tcp_port}/redirect')
        assert error.value.code == 302
        assert calls == ['redirect']
        assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1
    finally:
        await runner.cleanup()


def test_batch_limits_uncollected_futures_and_counts_errors(tasks, monkeypatch):
    outstanding = 0
    maximum = 0
    lock = threading.Lock()

    class TrackingExecutor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            nonlocal outstanding, maximum
            future = super().submit(*args, **kwargs)
            result = future.result
            with lock:
                outstanding += 1
                maximum = max(maximum, outstanding)

            def collect(*args, **kwargs):
                nonlocal outstanding
                try:
                    return result(*args, **kwargs)
                finally:
                    with lock:
                        outstanding -= 1

            future.result = collect
            return future

    def process(x, y, z, layer, params):
        return {'status': 'error' if x == 3 else 'success', 'tile': f'{z}/{x}/{y}'}

    monkeypatch.setattr(tasks, 'ThreadPoolExecutor', TrackingExecutor)
    monkeypatch.setattr(tasks, '_process_single_tile', process)
    result = tasks.tile_generate_batch.run([{'x': x, 'y': 34, 'z': 10} for x in range(12)], 'sentinel2', {'year': 2020})
    assert maximum <= 5
    assert result['successful'] == 11
    assert result['failed'] == 1
    assert [tile['tile'] for tile in result['tiles']] == [f'10/{x}/34' for x in range(12)]


def test_annual_dates_and_visualizations_do_not_overwrite_bytes(tasks, coordinator, monkeypatch):
    stored = {}
    monkeypatch.setattr(tasks, '_generate_sentinel2_tile', lambda _bounds, params: f'https://example.test/{params["year"]}/{params["vis_param"]}/{{z}}/{{x}}/{{y}}')
    monkeypatch.setattr(tasks.urllib.request.OpenerDirector, 'open', lambda self, request, data=None, timeout=None: DownloadResponse(request.full_url.encode()))
    monkeypatch.setattr(tasks, '_save_tile_to_redis', lambda key, data: stored.__setitem__(key, data))
    for year, visualization in [(2020, 'tvi-red'), (2021, 'tvi-red'), (2020, 'tvi-green')]:
        result = tasks._process_single_tile(12, 34, 10, 'sentinel2', {'year': year, 'vis_param': visualization})
        assert result['status'] == 'success'
    assert len(stored) == 3
    assert set(stored.values()) == {
        b'https://example.test/2020/tvi-red/10/12/34',
        b'https://example.test/2021/tvi-red/10/12/34',
        b'https://example.test/2020/tvi-green/10/12/34',
    }
    assert all(key.startswith('legacy-annual/') for key in stored)


def test_failed_s3_persistence_is_not_reported_as_success(tasks, coordinator, monkeypatch):
    persistence = ModuleType('app.cache.sync_cache')
    def fail_upload(*args):
        raise RuntimeError('S3 indisponível')
    persistence.persist_png_sync = fail_upload
    monkeypatch.setitem(sys.modules, 'app.cache.sync_cache', persistence)
    monkeypatch.setattr(tasks, '_get_sync_redis', lambda: fakeredis.FakeRedis(), raising=False)
    monkeypatch.setattr(tasks, '_generate_sentinel2_tile', lambda *_: 'https://example.test/{z}/{x}/{y}')
    monkeypatch.setattr(tasks.urllib.request.OpenerDirector, 'open', lambda self, request, data=None, timeout=None: DownloadResponse(b'PNG'))
    result = tasks._process_single_tile(12, 34, 10, 'sentinel2', {'year': 2020})
    assert result['status'] == 'error'
    assert result['error'] == 'S3 indisponível'


@pytest.mark.asyncio
async def test_mosaic_only_creates_coroutines_for_current_window(tasks, monkeypatch):
    window_sizes = []
    gather = asyncio.gather

    async def download(*args):
        return b'PNG'

    async def save(*args):
        return None

    async def track_gather(*coroutines, **kwargs):
        window_sizes.append(len(coroutines))
        return await gather(*coroutines, **kwargs)

    monkeypatch.setattr(tasks, '_download_tile', download)
    monkeypatch.setattr(tasks, 'aset_png', save, raising=False)
    monkeypatch.setattr(tasks, '_save_tile_to_redis', lambda *args: None)
    monkeypatch.setattr(tasks.asyncio, 'gather', track_gather)
    result = await tasks._process_mosaic_tiles('https://example.test/{z}/{x}/{y}',
        [{'x': x, 'y': 34, 'z': 10} for x in range(120)], 'sentinel2')
    assert max(window_sizes) <= 50
    assert len(result) == 120
    assert all(tile['status'] == 'success' for tile in result)


@pytest.mark.asyncio
async def test_mosaic_bounds_isolate_distinct_annual_products(tasks, monkeypatch):
    stored = {}
    async def download(url, *args):
        return url.encode()
    monkeypatch.setattr(tasks, '_download_tile', download)
    monkeypatch.setattr(tasks, '_save_tile_to_redis', lambda key, data: stored.__setitem__(key, data))
    tiles = [{'x': 12, 'y': 34, 'z': 10}]
    params = {'year': 2020, 'vis_param': 'tvi-red'}
    first = await tasks._process_mosaic_tiles('first', tiles, 'sentinel2', params,
        {'west': -50, 'east': -49, 'north': -10, 'south': -11})
    second = await tasks._process_mosaic_tiles('second', tiles, 'sentinel2', params,
        {'west': -51, 'east': -49, 'north': -10, 'south': -12})
    assert first[0]['status'] == second[0]['status'] == 'success'
    assert set(stored.values()) == {b'first', b'second'}
    assert len(stored) == 2


@pytest.mark.parametrize('mosaic', [False, True])
def test_celery_retry_respects_admission_retry_after(tasks, monkeypatch, mosaic):
    from app.core.gee_admission import GoogleAdmissionError
    error = GoogleAdmissionError(30, 'cooldown')
    def saturated(*args):
        raise error
    monkeypatch.setattr(tasks, '_generate_sentinel2_tile', saturated)
    delays = []
    task = tasks.tile_generate_mosaic if mosaic else tasks.tile_generate
    def retry(*, exc, countdown):
        delays.append(countdown)
        raise exc
    monkeypatch.setattr(task, 'retry', retry)
    with pytest.raises(GoogleAdmissionError):
        if mosaic:
            task.run({'west': -50, 'east': -49, 'north': -10, 'south': -11}, 3, 'sentinel2', {'year': 2020})
        else:
            task.run(10, 12, 34, 'sentinel2', {'year': 2020})
    assert delays == [30]
