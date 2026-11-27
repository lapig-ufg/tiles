import json
from pathlib import Path
import pytest
from app.cache.campaign_manifest import viewport_tiles

CONTRACT = json.loads((Path(__file__).parents[1] / 'fixtures/leaflet_tile_contract.json').read_text())


@pytest.mark.parametrize('case', CONTRACT['cases'])
def test_manifest_contains_leaflet_requested_tiles(case):
    requested = {(t['x'], t['y'], t['z']) for t in case['tiles']}
    prepared = {(t['x'], t['y'], t['z']) for t in viewport_tiles(
        case['lat'], case['lon'], [case['z']], case['width'], case['height'])}
    assert requested <= prepared


@pytest.mark.parametrize('sensor', ['landsat', 'sentinel-2'])
@pytest.mark.parametrize('case', CONTRACT['cases'])
def test_gee_manifest_preserves_client_coordinates_and_storage_key(sensor, case):
    from app.cache.campaign_manifest import campaign_config, manifest_for_point
    from app.cache.keys import build_tile_cache_keys
    from app.services.tile import tile2goehashBBOX

    campaign = dict(imageType=sensor, useDynamicMaps=True, initialYear=2020,
                    finalYear=2020, zoomLevel=case['z'],
                    visParams=['landsat-tvi-false', 'landsat-tvi-true'],
                    defaultVisParam='landsat-tvi-false')
    config = campaign_config(campaign, width=case['width'], height=case['height'])
    items = list(manifest_for_point(dict(_id='point', lat=case['lat'], lon=case['lon']), config))
    requested = {(t['x'], t['y'], t['z']) for t in case['tiles']}
    for period in ('WET', 'DRY'):
        available = {(i['params']['x'], i['params']['y'], i['params']['z']): i
                     for i in items if i['params']['period'] == period}
        assert requested <= available.keys()
        for xyz in requested:
            item = available[xyz]
            geohash, _ = tile2goehashBBOX(*xyz)
            assert item['key'] == build_tile_cache_keys(**item['params'], geohash=geohash).png
            assert item['params']['visparam'] == campaign['defaultVisParam']
