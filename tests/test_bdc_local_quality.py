import numpy as np
import pytest

from app.modules.bdc.local_quality import quality_metrics, rank_rows, point_buffer


def test_cloudy_scene_with_clear_buffer_wins():
    rows = [dict(id='global', scene_cloud_pct=10, **quality_metrics(np.full((4, 4), 8, dtype='uint16'), np.ones((4, 4), bool), 'landsat-2')),
            dict(id='local', scene_cloud_pct=90, **quality_metrics(np.full((4, 4), 64, dtype='uint16'), np.ones((4, 4), bool), 'landsat-2'))]
    assert rank_rows(rows, 'cloud')[0]['id'] == 'local'


def test_nodata_cannot_win_and_partial_coverage_is_ineligible():
    inside = np.ones((2, 2), bool)
    empty = quality_metrics(np.ones((2, 2), dtype='uint16'), inside, 'landsat-2')
    partial = quality_metrics(np.array([[64, 64], [64, 1]]), inside, 'landsat-2')
    assert empty['cloud_pct'] is None
    assert not empty['eligible'] and not partial['eligible']
    assert partial['valid_pct'] == 75


def test_landsat_cloud_shadow_cirrus_and_snow_are_separate():
    data = np.array([[8, 16, 4, 32, 64]], dtype='uint16')
    result = quality_metrics(data, np.ones_like(data, bool), 'landsat-2')
    assert result['cloud_pct'] == 40
    assert result['shadow_pct'] == 20
    assert result['unusable_pct'] == 80
    assert result['legacy_cloud_snow_count'] == 2


def test_sentinel_scl_classes_and_nodata():
    data = np.array([[0, 1, 3, 4, 8, 9, 10, 11]], dtype='uint8')
    result = quality_metrics(data, np.ones_like(data, bool), 'S2_L2A-1')
    assert result['valid_pct'] == 75
    assert result['cloud_pct'] == 50
    assert result['shadow_pct'] == pytest.approx(100 / 6)


def test_ties_are_deterministic_and_unsupported_rejected():
    rows = [dict(id=x, eligible=True, cloud_pct=0, unusable_pct=0, valid_pct=100, scene_cloud_pct=0) for x in ['b', 'a']]
    assert [r['id'] for r in rank_rows(rows, 'cloud')] == ['a', 'b']
    with pytest.raises(ValueError):
        quality_metrics(np.zeros((2, 2)), np.ones((2, 2), bool), 'unknown')
    with pytest.raises(ValueError):
        point_buffer(float('nan'), 0, 1000)


def test_catalog_does_not_filter_global_cloud_and_includes_end_date():
    from urllib.parse import parse_qs, urlsplit
    from app.modules.bdc.local_quality import search_candidates
    calls = []
    class Remote:
        def json(self, url):
            calls.append(parse_qs(urlsplit(url).query))
            if len(calls) == 1:
                return {'features': [{'id': 'cloudy', 'properties': {'eo:cloud_cover': 99}}], 'links': [{'rel': 'next'}]}
            return {'features': [{'id': 'clear'}], 'links': []}
    rows = search_candidates(Remote(), 'landsat-2', point_buffer(-49.4, -16.7, 1000), '2024-01-01', '2024-01-31')
    assert len(rows) == 2
    assert all('query' not in query for query in calls)
    assert calls[0]['datetime'][0].endswith('2024-01-31T23:59:59.999999Z')


def test_catalog_rejects_partial_and_repeated_pages():
    from app.modules.bdc.local_quality import search_candidates
    class Remote:
        def json(self, url):
            return {'features': [{'id': 'a'}, {'id': 'b'}], 'links': [{'rel': 'next'}]}
    geom = point_buffer(-49.4, -16.7, 1000)
    with pytest.raises(ValueError, match='Mais de'):
        search_candidates(Remote(), 'landsat-2', geom, '2024-01-01', '2024-01-31', max_candidates=1)
    with pytest.raises(ValueError, match='Paginação'):
        search_candidates(Remote(), 'landsat-2', geom, '2024-01-01', '2024-01-31')


def test_actual_raster_window_masks_outside_pixels(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    from app.modules.bdc import local_quality
    from shapely.geometry import box
    data = np.full((10, 10), 8, dtype='uint16')
    data[3:7, 3:7] = 64
    path = tmp_path / 'quality.tif'
    with rasterio.open(path, 'w', driver='GTiff', height=10, width=10, count=1,
                       dtype='uint16', crs='EPSG:4326', transform=from_origin(0, 10, 1, 1)) as dst:
        dst.write(data, 1)
    class Bridge:
        def __init__(self, remote): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def url(self, url): return url
    monkeypatch.setattr(local_quality, 'RangeBridge', Bridge)
    item = {'assets': {'qa_pixel': {'href': str(path)}}}
    result = local_quality.read_quality(None, item, box(3, 3, 7, 7), 'landsat-2')
    assert result['eligible'] and result['cloud_pct'] == 0
    assert result['buffer_pixels'] == 16
    outside = local_quality.read_quality(None, item, box(-1, 3, 2, 7), 'landsat-2')
    assert not outside['eligible']


def test_api_validates_and_forwards_point_parameters(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.modules.bdc.router import create_router
    calls = []
    class Service:
        root = tmp_path
        def best_images(self, *args):
            calls.append(args)
            return {'best_local_id': 'example', 'complete': True}
    app = FastAPI()
    app.include_router(create_router(Service()))
    client = TestClient(app)
    query = dict(collection='landsat-2', lon=-49.4, lat=-16.7, radius_m=1000, start='2024-01-01', end='2024-01-31')
    response = client.get('/best-images', params=query)
    assert response.status_code == 200 and response.json()['complete']
    assert calls[0][1:4] == (-49.4, -16.7, 1000)
    assert client.get('/best-images', params={**query, 'radius_m': 0}).status_code == 422
    assert client.get('/best-images', params={**query, 'lon': 'nan'}).status_code == 422
    assert client.get('/best-images', params={**query, 'collection': 'other'}).status_code == 422
