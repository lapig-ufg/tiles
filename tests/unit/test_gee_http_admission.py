import asyncio
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import fakeredis
import pytest
from aiohttp import web


@pytest.fixture
def coordinator(monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    controller = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True),
        admission.AdmissionLimits(wait_seconds=0),
    )
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: controller)
    return controller


@asynccontextmanager
async def endpoint(handler, port):
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_retry_consumes_budget_for_each_attempt(coordinator, unused_tcp_port):
    from app.utils.http import http_get_bytes

    calls = []

    async def handle(request):
        calls.append(request.path)
        if len(calls) == 1:
            request.transport.close()
            return web.Response()
        return web.Response(body=b'PNG')

    async with endpoint(handle, unused_tcp_port) as base:
        assert await http_get_bytes(base + '/tile', base_delay=0) == b'PNG'
    assert calls == ['/tile', '/tile']
    assert coordinator.redis.zcard('gee:{admission}:shared:rate') == 2
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0


@pytest.mark.asyncio
async def test_http_429_prevents_other_consumer_before_network(coordinator, unused_tcp_port):
    from app.core.gee_admission import GoogleAdmissionError
    from app.utils.http import EarthEngineRateLimitedError, http_get_bytes

    calls = []

    async def handle(request):
        calls.append(request.path)
        return web.Response(status=429, headers={'Retry-After': '30'})

    async with endpoint(handle, unused_tcp_port) as base:
        with pytest.raises(EarthEngineRateLimitedError):
            await http_get_bytes(base + '/tile', max_retries=1)
        with pytest.raises(GoogleAdmissionError) as error:
            await http_get_bytes(base + '/other')
    assert error.value.retry_after > 29
    assert calls == ['/tile']
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0


@pytest.mark.asyncio
async def test_http_cancel_closes_network_before_freeing_capacity(coordinator, unused_tcp_port):
    from app.utils.http import http_get_bytes

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def handle(request):
        entered.set()
        await finish.wait()
        return web.Response(body=b'PNG')

    async with endpoint(handle, unused_tcp_port) as base:
        task = asyncio.create_task(http_get_bytes(base + '/tile'))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0
        finish.set()


@pytest.mark.asyncio
async def test_http_timeout_releases_lease(coordinator, unused_tcp_port):
    from fastapi import HTTPException
    from app.utils.http import http_get_bytes

    finish = asyncio.Event()

    async def handle(request):
        await finish.wait()
        return web.Response(body=b'PNG')

    async with endpoint(handle, unused_tcp_port) as base:
        try:
            with pytest.raises(HTTPException) as error:
                await http_get_bytes(base + '/tile', max_retries=1, timeout=.03)
            assert error.value.status_code == 503
            assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0
        finally:
            finish.set()


@pytest.mark.asyncio
async def test_compute_keeps_credentials_and_project_after_429(coordinator, unused_tcp_port, monkeypatch):
    from app.utils import ee_compute
    from app.core.gee_admission import GoogleAdmissionError

    calls = []

    async def handle(request):
        calls.append((request.path, request.headers['Authorization'],
                      request.headers['X-Goog-User-Project']))
        return web.Response(status=429, headers={'Retry-After': '30'})

    creds = SimpleNamespace(valid=True, token='test-token', project_id='actual-project')
    async with endpoint(handle, unused_tcp_port) as base:
        monkeypatch.setattr(ee_compute, '_BASE_URL', base)
        try:
            with pytest.raises(GoogleAdmissionError):
                await ee_compute.compute_value(1, credentials=creds, base_delay=0)
        finally:
            await ee_compute.close_session()
    assert calls == [('/projects/actual-project/value:compute', 'Bearer test-token', 'actual-project')]


def test_map_registration_accounts_project_and_shared_pause(coordinator, monkeypatch):
    import requests
    from app.core.gee_admission import GoogleAdmissionError
    from app.utils.ee_maps import create_map_with_credential

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs['headers']))
        return SimpleNamespace(status_code=429, headers={'Retry-After': '20'}, text='rate limit')

    monkeypatch.setattr(requests, 'post', post)
    image = SimpleNamespace(visualize=lambda **kwargs: 1)
    credential = {'project_id': 'campaign-project', 'access_token': 'test-token'}
    with pytest.raises(Exception):
        create_map_with_credential(image, {}, credential)
    with pytest.raises(GoogleAdmissionError):
        create_map_with_credential(image, {}, credential)
    assert len(calls) == 1
    assert calls[0][1]['X-Goog-User-Project'] == 'campaign-project'
    assert calls[0][1]['Authorization'] == 'Bearer test-token'


@pytest.mark.asyncio
async def test_429_preserves_cached_url_and_does_not_regenerate(coordinator, unused_tcp_port, monkeypatch):
    from app.utils import ee_tile_fetch
    from app.utils.http import EarthEngineRateLimitedError, http_get_bytes

    calls = []
    regenerated = []

    async def handle(request):
        calls.append(request.path)
        return web.Response(status=429, headers={'Retry-After': '30'})

    async def one_attempt(url):
        return await http_get_bytes(url, max_retries=1)

    async def factory():
        regenerated.append(True)
        return 'invalid'

    monkeypatch.setattr(ee_tile_fetch, 'http_get_bytes', one_attempt)
    async with endpoint(handle, unused_tcp_port) as base:
        with pytest.raises(EarthEngineRateLimitedError):
            await ee_tile_fetch.fetch_tile_with_rotation(
                cache_key='landsat/key', cached_url=base + '/{z}/{x}/{y}',
                url_factory=factory, x=1, y=2, z=3, layer='landsat',
            )
    assert regenerated == []
    assert calls == ['/3/1/2']


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['download', 'compute', 'map'])
async def test_redirect_nao_produz_chamada_fora_do_orcamento(coordinator, unused_tcp_port, monkeypatch, operation):
    from app.utils import ee_compute, ee_maps
    from app.utils.http import http_get_bytes
    calls = []

    async def handle(request):
        calls.append(request.path)
        if request.path != '/redirected':
            return web.Response(status=302, headers={'Location': '/redirected'})
        return web.json_response({'result': 1, 'name': 'projects/p/maps/m'})

    async with endpoint(handle, unused_tcp_port) as base:
        try:
            with pytest.raises(Exception):
                if operation == 'download':
                    await http_get_bytes(base + '/tile', max_retries=1)
                elif operation == 'compute':
                    monkeypatch.setattr(ee_compute, '_BASE_URL', base)
                    await ee_compute.compute_value(
                        1, credentials=SimpleNamespace(valid=True, token='test-token', project_id='p'),
                        max_retries=1,
                    )
                else:
                    monkeypatch.setattr(ee_maps, '_EE_BASE', base)
                    await asyncio.to_thread(
                        ee_maps.create_map_with_credential,
                        SimpleNamespace(visualize=lambda **kwargs: 1), {},
                        {'project_id': 'p', 'access_token': 'test-token'},
                    )
        finally:
            await ee_compute.close_session()
    assert len(calls) == 1
    assert '/redirected' not in calls
    assert coordinator.redis.zcard('gee:{admission}:shared:rate') == 1
