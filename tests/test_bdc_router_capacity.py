import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.bdc import offline_tiles
from app.modules.bdc.router import create_router
from app.modules.bdc.selection_jobs import OriginBusy
from app.modules.bdc.service import BdcService
from app.modules.bdc.transport import ServicePaused


@pytest.mark.parametrize(
    'error,status,retry_after',
    [(OriginBusy('busy'), 503, '2'), (ServicePaused('paused'), 503, None),
     (RuntimeError('unavailable'), 502, None), (ValueError('invalid'), 422, None)],
)
def test_tile_failure_classification(tmp_path, monkeypatch, error, status, retry_after):
    service = BdcService(tmp_path)

    def fail(*args):
        raise error

    monkeypatch.setattr(offline_tiles, 'read_tile', lambda *args: None)
    monkeypatch.setattr(service, 'render', fail)
    app = FastAPI()
    app.include_router(create_router(service), prefix='/api/bdc')
    response = TestClient(app).get('/api/bdc/tiles/c/i/14/5943/8963.png?profile=rgb')
    assert response.status_code == status
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers.get('retry-after') == retry_after


def test_shared_cached_tile_is_served_while_origin_paused(tmp_path, monkeypatch):
    import io
    from PIL import Image

    image = io.BytesIO()
    Image.new('RGBA', (256, 256), (10, 20, 30, 255)).save(image, format='PNG')
    data = image.getvalue()
    service = BdcService(tmp_path)
    monkeypatch.setattr(offline_tiles, 'read_tile', lambda *args: (data, 2000000000))

    def paused(*args):
        raise ServicePaused('paused')

    monkeypatch.setattr(service, 'detail', paused)
    app = FastAPI()
    app.include_router(create_router(service), prefix='/api/bdc')
    response = TestClient(app).get('/api/bdc/tiles/c/i/14/5943/8963.png?profile=rgb')
    assert response.status_code == 200
    assert response.content == data
    assert response.headers['x-cache'] == 'HIT'
    assert response.headers['x-shared-cache'] == 'HIT'
    assert response.headers['x-range-requests'] == '0'
    assert response.headers['content-type'] == 'image/png'


def test_warm_tile_bypasses_local_generation_admission_but_misses_do_not(tmp_path, monkeypatch):
    from app.modules.bdc.admission import admission
    service = BdcService(tmp_path)
    data = b'validated-warm-png'
    monkeypatch.setattr(offline_tiles, 'read_tile', lambda collection, item, *args: (data, 2000000000) if item == 'warm' else None)
    monkeypatch.setattr(service, 'render', lambda *args: pytest.fail('Nenhuma geração deve começar sem admissão'))
    app = FastAPI()
    app.include_router(create_router(service), prefix='/api/bdc')
    client = TestClient(app)
    with admission(tmp_path), admission(tmp_path):
        hit = client.get('/api/bdc/tiles/c/warm/14/5943/8963.png?profile=rgb')
        miss = client.get('/api/bdc/tiles/c/cold/14/5943/8963.png?profile=rgb')
        invalid = client.get('/api/bdc/tiles/c/warm/14/5943/8963.png?profile=../invalid')
        jpeg = client.get('/api/bdc/tiles/c/warm/14/5943/8963.jpeg?profile=rgb')
    assert hit.status_code == 200
    assert hit.content == data
    assert hit.headers['x-cache'] == 'HIT'
    assert hit.headers['x-range-requests'] == '0'
    assert miss.status_code == 503 and miss.headers['retry-after'] == '2'
    assert invalid.status_code == 422
    assert jpeg.status_code == 503


def test_warm_tile_is_served_with_both_router_render_slots_occupied(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    service = BdcService(tmp_path)
    entered, release = Barrier(3), Event()
    monkeypatch.setattr(offline_tiles, 'read_tile', lambda collection, item, *args: (b'warm', 2000000000) if item == 'warm' else None)

    def render(*args):
        entered.wait(timeout=3)
        assert release.wait(3)
        return b'generated', dict(key='generated', cache='MISS', ms=1, ranges=1, bytes=1)

    monkeypatch.setattr(service, 'render', render)
    app = FastAPI()
    app.include_router(create_router(service), prefix='/api/bdc')
    client = TestClient(app)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(client.get, '/api/bdc/tiles/c/cold/14/5943/8963.png?profile=rgb') for _ in range(2)]
        try:
            entered.wait(timeout=3)
            response = client.get('/api/bdc/tiles/c/warm/14/5943/8963.png?profile=rgb')
            rejected = client.get('/api/bdc/tiles/c/another/14/5943/8963.png?profile=rgb')
            assert response.status_code == 200
            assert response.content == b'warm'
            assert rejected.status_code == 503
        finally:
            release.set()
            assert all(future.result(timeout=3).status_code == 200 for future in pending)
