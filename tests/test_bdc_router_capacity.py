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
    assert response.headers['x-range-requests'] == '0'
    assert response.headers['content-type'] == 'image/png'
