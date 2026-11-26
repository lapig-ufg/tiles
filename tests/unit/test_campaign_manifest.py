import pytest


def campaign(**changes):
    return dict({'imageType': 'landsat', 'useDynamicMaps': True, 'initialYear': 2020,
                 'finalYear': 2021, 'zoomLevel': 17,
                 'visParamsEnable': ['landsat-tvi-false', 'landsat-tvi-agri']}, **changes)


def test_manifest_covers_periods_years_visualizations_and_allowed_zooms():
    from app.cache.campaign_manifest import manifest_for_point, campaign_config
    items = list(manifest_for_point({'_id': 'p1', 'lat': 0, 'lon': 0}, campaign_config(campaign())))
    assert {i['params']['period'] for i in items} == {'WET', 'DRY'}
    assert {i['params']['year'] for i in items} == {2020, 2021}
    assert {i['params']['z'] for i in items} == {17, 18}
    assert len(items) == len({i['key'] for i in items})
    assert len(items) > 2 * 2 * 2 * 2


def test_viewport_covers_leaflet_initial_tiles_and_buffer():
    from app.cache.campaign_manifest import viewport_tiles
    tiles = {(t['x'], t['y']) for t in viewport_tiles(0, 0, [10], 512, 512)}
    assert {(x, y) for x in range(509, 515) for y in range(509, 515)} <= tiles
    assert all(0 <= x < 1024 and 0 <= y < 1024 for x, y in tiles)


@pytest.mark.parametrize('lat,lon', [(90, 0), (-90, 0), (0, 180), (0, -180)])
def test_boundary_coordinates_are_clamped_and_wrapped(lat, lon):
    from app.cache.campaign_manifest import viewport_tiles
    tiles = list(viewport_tiles(lat, lon, [10], 512, 512))
    assert tiles
    assert all(0 <= t['x'] < 1024 and 0 <= t['y'] < 1024 for t in tiles)
    assert len(tiles) == len({(t['x'], t['y']) for t in tiles})


@pytest.mark.parametrize('changes', [
    {'imageType': 'wms'}, {'useDynamicMaps': False}, {'initialYear': 2022},
    {'zoomLevel': 19}, {'visParamsEnable': []}, {'visParamsEnable': ['../bad']},
])
def test_configuration_rejects_unsupported_or_invalid_campaigns(changes):
    from app.cache.campaign_manifest import campaign_config
    with pytest.raises(ValueError):
        campaign_config(campaign(**changes))


def test_sentinel_uses_exact_api_layer():
    from app.cache.campaign_manifest import campaign_config
    assert campaign_config(campaign(imageType='sentinel-2'))['layer'] == 's2_harmonized'


def test_manifest_uses_visualizations_persisted_by_tvi_campaign_form():
    from app.cache.campaign_manifest import campaign_config, manifest_for_point
    stored = campaign()
    stored.pop('visParamsEnable')
    stored['visParams'] = ['landsat-tvi-false']
    stored['defaultVisParam'] = 'landsat-tvi-false'
    config = campaign_config(stored)
    items = list(manifest_for_point({'_id': 'p1', 'lat': 0, 'lon': 0}, config))
    assert {item['params']['visparam'] for item in items} == {'landsat-tvi-false'}


def test_tvi_visualizations_take_precedence_over_legacy_field():
    from app.cache.campaign_manifest import campaign_config
    assert campaign_config(campaign(visParams=['landsat-tvi-true']))['visualizations'] == ['landsat-tvi-true']


@pytest.mark.parametrize('value', [[], None, 'landsat-tvi-false', ['../bad']])
def test_invalid_tvi_visualizations_do_not_fall_back_to_legacy_field(value):
    from app.cache.campaign_manifest import campaign_config
    with pytest.raises(ValueError):
        campaign_config(campaign(visParams=value))
