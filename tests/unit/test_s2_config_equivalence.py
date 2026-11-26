import copy
import importlib
from types import SimpleNamespace

import pymongo
import pytest

from tests.unit.test_landsat_config_equivalence import catalog
from tests.unit.test_landsat_image_equivalence import DATES, configure_expression_recorder


@pytest.fixture
def sentinel_catalog(catalog, monkeypatch):
    catalog.document = {
        '_id': 'tvi-custom', 'name': 'tvi-custom', 'display_name': 'Visualização configurada',
        'category': 'sentinel', 'active': True,
        'band_config': {'original_bands': ['B8', 'B4'], 'mapped_bands': ['NIR', 'RED']},
        'vis_params': {'bands': ['NIR', 'RED'], 'min': [0.1, 0.2],
                       'max': [0.8, 0.9], 'gamma': [1.2]},
    }
    monkeypatch.setattr(catalog.loader, 'get_VISPARAMS_sync', lambda: {})
    catalog.layers, catalog.worker, catalog.payloads = configure_expression_recorder(monkeypatch)
    return catalog


@pytest.mark.asyncio
@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('campaign', [None, 'campaign'])
async def test_s2_worker_matches_api_expression_and_visual_payload_from_mongo(
    sentinel_catalog, monkeypatch, indexed, campaign,
):
    state = sentinel_catalog
    if indexed:
        state.document['vis_params'].update({
            'min': [-0.2], 'max': [0.9], 'palette': ['#a52a2a', '#006837'],
            'index': {'type': 'normalized_difference', 'bands': ['NIR', 'RED'], 'name': 'NDVI'},
        })
    original = copy.deepcopy(state.document)
    module = importlib.import_module('app.visualization.vis_params_db')

    class Collection:
        def find(self, query):
            async def documents():
                yield copy.deepcopy(state.document)
            return documents()

        async def find_one(self, query):
            return None

    monkeypatch.setattr(module, 'get_database', lambda: SimpleNamespace(vis_params=Collection()))
    monkeypatch.setattr(module, 'vis_params_manager', module.VisParamsManager())
    api_config = (await module.get_visparams_dict())['tvi-custom']
    monkeypatch.setattr(state.worker, 'get_campaign_token', lambda campaign: {'project_id': 'project'})

    def credential_map(image, parameters, credential, **kwargs):
        state.payloads.append({'image': image.tree, **copy.deepcopy(parameters)})
        return 'https://example.test/map'

    monkeypatch.setattr(state.worker, 'create_map_with_credential', credential_map)
    state.layers._create_s2_layer_sync('geometry', DATES, api_config)
    state.worker._warm_create_s2_url('geometry', DATES, 'tvi-custom', campaign)
    assert state.payloads[0] == state.payloads[1]
    assert state.payloads[1]['bands'] == (['NDVI'] if indexed else ['NIR', 'RED'])
    assert state.payloads[1]['min'] == ('-0.2' if indexed else '0.1,0.2')
    assert state.closed == 1
    assert state.document == original


@pytest.mark.parametrize('condition', ['missing', 'inactive', 'no_bands', 'no_visual'])
def test_s2_invalid_catalog_never_falls_back_or_registers_a_map(sentinel_catalog, condition):
    state = sentinel_catalog
    if condition == 'missing':
        state.document = None
    elif condition == 'inactive':
        state.document['active'] = False
    elif condition == 'no_bands':
        state.document.pop('band_config')
    else:
        state.document.pop('vis_params')
    with pytest.raises((ValueError, KeyError)):
        state.worker._warm_create_s2_url('geometry', DATES, 'tvi-custom')
    assert state.payloads == []
    assert state.closed == 1


def test_s2_catalog_failure_propagates_and_closes_client(sentinel_catalog):
    state = sentinel_catalog
    state.failure = pymongo.errors.ServerSelectionTimeoutError('catálogo indisponível')
    with pytest.raises(pymongo.errors.ServerSelectionTimeoutError):
        state.worker._warm_create_s2_url('geometry', DATES, 'tvi-custom')
    assert state.payloads == []
    assert state.closed == 1


def test_s2_disabled_mongo_uses_exact_static_visual_source(sentinel_catalog, monkeypatch):
    state = sentinel_catalog
    monkeypatch.setattr(state.loader, 'USE_MONGODB_VIS_PARAMS', False)
    hardcoded = importlib.import_module('app.visualization.visParam')
    config = {'select': (['B8', 'B4'], ['NIR', 'RED']),
              'visparam': {'bands': ['NIR', 'RED'], 'min': [0.1, 0.2], 'max': [0.8, 0.9]}}
    original = copy.deepcopy(config)
    monkeypatch.setattr(hardcoded, 'VISPARAMS', {'tvi-custom': config})
    state.layers._create_s2_layer_sync('geometry', DATES, config)
    state.worker._warm_create_s2_url('geometry', DATES, 'tvi-custom')
    assert state.payloads[0] == state.payloads[1]
    assert config == original
    assert state.queries == []


def test_s2_unknown_static_visual_does_not_use_tvi_red(sentinel_catalog, monkeypatch):
    state = sentinel_catalog
    monkeypatch.setattr(state.loader, 'USE_MONGODB_VIS_PARAMS', False)
    hardcoded = importlib.import_module('app.visualization.visParam')
    monkeypatch.setattr(hardcoded, 'VISPARAMS', {'tvi-red': {'select': ['B4'], 'visparam': {}}})
    with pytest.raises((ValueError, KeyError)):
        state.worker._warm_create_s2_url('geometry', DATES, 'unknown')
    assert state.payloads == []
    assert state.queries == []
