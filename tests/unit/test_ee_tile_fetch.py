import importlib

import fakeredis
import pytest
from aiohttp import web
from fastapi import HTTPException


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 401, 429, 503])
async def test_tile_preserves_url_and_limits_external_work(status, unused_tcp_port, monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    coordinator = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True), admission.AdmissionLimits(wait_seconds=0),
    )
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    cache = importlib.import_module('app.cache.cache')
    metadata = {'tile/key': {'url': 'cached-url'}}

    async def delete_meta(key):
        metadata.pop(key, None)

    async def set_meta(key, value):
        metadata[key] = value

    monkeypatch.setattr(cache, 'adelete_meta', delete_meta)
    monkeypatch.setattr(cache, 'aset_meta', set_meta)
    fetch = importlib.import_module('app.utils.ee_tile_fetch').fetch_tile_with_rotation
    calls = []
    generated = []

    async def handle(request):
        calls.append(request.path)
        return web.Response(status=status, body=b'PNG', headers={'Retry-After': '30'})

    async def factory():
        generated.append(True)
        return 'regenerated-url'

    app = web.Application()
    app.router.add_get('/{z}/{x}/{y}', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', unused_tcp_port).start()
    try:
        arguments = dict(
            cache_key='tile/key',
            cached_url=f'http://127.0.0.1:{unused_tcp_port}/{{z}}/{{x}}/{{y}}',
            url_factory=factory, x=1, y=2, z=3, layer='landsat',
        )
        if status == 200:
            assert await fetch(**arguments) == b'PNG'
        elif status == 429:
            with pytest.raises(admission.GoogleAdmissionError):
                await fetch(**arguments)
        else:
            with pytest.raises(HTTPException) as error:
                await fetch(**arguments)
            assert error.value.status_code == status
    finally:
        await runner.cleanup()

    assert calls == ['/3/1/2']
    assert generated == []
    assert metadata == {'tile/key': {'url': 'cached-url'}}
