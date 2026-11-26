import asyncio
import io
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager

import fakeredis
import httpx
import pytest
from PIL import Image

from tests.integration.test_circuit_breaker_integration import app_with_cb
from tests.integration.test_tile_cache_reuse import generation_context


@pytest.mark.asyncio
@pytest.mark.parametrize('limited', [False, True])
@pytest.mark.parametrize('concurrency', [30, 76])
async def test_38_years_six_tiles_without_cache(app_with_cb, monkeypatch, limited, concurrency):
    from app.core import gee_admission as admission

    layers, _, _ = generation_context(monkeypatch)
    controller = admission.RedisAdmission(fakeredis.FakeRedis(), admission.AdmissionLimits())
    metadata, images, locks = {}, {}, {}
    active = peak = 0
    mutex = threading.Lock()
    attempts = []
    generated = Counter()
    buffer = io.BytesIO()
    Image.new('RGB', (256, 256)).save(buffer, format='JPEG')
    payload = buffer.getvalue()

    @asynccontextmanager
    async def lock(key):
        async with locks.setdefault(key, asyncio.Lock()):
            yield True

    async def get_png(key):
        return images.get(key)

    async def get_meta(key):
        return metadata.get(key)

    async def set_meta(key, value):
        metadata[key] = value

    async def set_png(key, value, **kwargs):
        images[key] = value

    def build(geom, dates, *args):
        nonlocal active, peak
        lease = controller.acquire(project='test-project', operation='map')
        assert controller.redis.zcard('gee:{admission}:shared:rate') <= 50
        try:
            with mutex:
                active += 1
                peak = max(peak, active)
                attempts.append(time.monotonic())
                generated[dates['dtStart']] += 1
            time.sleep(.01)
            return 'https://ee/generated/{z}/{x}/{y}'
        finally:
            with mutex:
                active -= 1
            lease.release()

    injected = False

    async def fetch(**kwargs):
        nonlocal active, peak, injected
        for _ in range(2):
            lease = await controller.acquire_async(project='test-project')
            assert controller.redis.zcard('gee:{admission}:shared:rate') <= 50
            try:
                with mutex:
                    active += 1
                    peak = max(peak, active)
                    attempts.append(time.monotonic())
                if limited and not injected:
                    injected = True
                    lease.response(429, '.1')
                    continue
                await asyncio.sleep(.02)
                return payload
            finally:
                with mutex:
                    active -= 1
                lease.release()
        raise AssertionError('Download não concluído')

    for name, value in dict(tile_lock=lock, get_png=get_png, get_meta=get_meta,
                            set_meta=set_meta, set_png=set_png,
                            _create_landsat_layer_with_params=build,
                            fetch_tile_with_rotation=fetch).items():
        monkeypatch.setattr(layers, name, value)
    slots = asyncio.Semaphore(concurrency)
    urls = [f'/landsat/{12 + offset}/34/10?period=DRY&year={year}&visparam=landsat-tvi-agri'
            for year in range(1985, 2023) for offset in range(6)]
    started = time.monotonic()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_with_cb), base_url='http://tiles') as client:
        async def load(url):
            async with slots:
                for attempt in range(4):
                    response = await client.get(url)
                    if response.status_code != 503 or attempt == 3:
                        break
                    await asyncio.sleep(max(float(response.headers['Retry-After']), (3, 8, 15)[attempt]))
                assert response.status_code == 200, (url, response.status_code, response.headers)
                assert response.content == payload
                return time.monotonic() - started
        durations = await asyncio.gather(*(load(url) for url in urls))
    assert len(images) == 228
    assert len(generated) == 38
    assert set(generated.values()) == {1}
    assert peak <= 20
    assert len(attempts) == 266 + int(limited)
    assert controller.redis.zcard('gee:{admission}:shared:active') == 0
    assert max(durations) < 40
    print(f'cold_tiles=228 first={min(durations):.3f}s total={max(durations):.3f}s peak={peak} limited={limited} concurrency={concurrency}')
