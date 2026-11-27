import pytest
from app.cache.campaign_manifest import campaign_config


def campaign(**changes):
    return dict(dict(imageType='landsat', useDynamicMaps=True, initialYear=2020,
                     finalYear=2021, zoomLevel=13,
                     visParams=['landsat-tvi-false', 'landsat-tvi-true'],
                     defaultVisParam='landsat-tvi-true'), **changes)


@pytest.mark.parametrize('sensor', ['landsat', 'sentinel-2'])
def test_gee_prepares_only_configured_zoom_and_default(sensor):
    result = campaign_config(campaign(imageType=sensor))
    assert result['zooms'] == [13]
    assert result['visualizations'] == ['landsat-tvi-true']
    assert result['version'] == 2


def test_missing_legacy_default_preserves_list_order():
    value = campaign(visParams=['z-profile', 'a-profile'])
    value.pop('defaultVisParam')
    assert campaign_config(value)['visualizations'] == ['z-profile']


def test_explicit_invalid_default_is_rejected():
    with pytest.raises(ValueError):
        campaign_config(campaign(defaultVisParam='missing'))


def test_singular_legacy_visualization_is_supported():
    value = campaign(visParam='landsat-tvi-false')
    value.pop('visParams')
    value.pop('defaultVisParam')
    assert campaign_config(value)['visualizations'] == ['landsat-tvi-false']


def test_bdc_default_does_not_remove_other_available_profiles():
    value = campaign(imagerySource='bdc', bdcConfig=dict(
        schemaVersion=1, collection='landsat-2', selectionRevision=1,
        bufferRadiusM=1000, periodPolicy='tvi-seasonal-v1', selectionStrategy='local_cloud',
        profiles=['landsat-tvi-false', 'landsat-tvi-true'], defaultProfile='landsat-tvi-true'))
    result = campaign_config(value)
    assert result['visualizations'] == ['landsat-tvi-true']
    assert result['zooms'] == [13]
    assert result['bdc']['profiles'] == value['bdcConfig']['profiles']


def test_null_default_from_legacy_form_uses_first_visualization():
    assert campaign_config(campaign(defaultVisParam=None))['visualizations'] == ['landsat-tvi-false']
