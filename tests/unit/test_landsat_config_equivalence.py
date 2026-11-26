import copy
import importlib
from types import SimpleNamespace

import pymongo
import pytest


@pytest.fixture
def catalog(monkeypatch):
    state = SimpleNamespace(
        mapping={'_id': 'landsat_collections', 'mappings': [
            {'start_year': 1985, 'end_year': 2030, 'collection': 'LANDSAT/CONFIGURADA'}]},
        document={
            '_id': 'landsat-tvi-false', 'name': 'landsat-tvi-false',
            'display_name': 'Visualização configurada', 'category': 'landsat', 'active': True,
            'satellite_configs': [
                {'collection_id': collection, 'vis_params': {
                    'bands': ['CUSTOM_1', 'CUSTOM_2', 'CUSTOM_3'],
                    'min': [0.12, 0.23, 0.34], 'max': [0.7, 0.8, 0.9], 'gamma': [1.25],
                }} for collection in ['LANDSAT/CONFIGURADA', 'LANDSAT/LC08/C02/T1_L2']
            ],
        },
        closed=0, queries=[], failure=None,
    )

    class Collection:
        def find_one(self, query):
            state.queries.append(query)
            if state.failure:
                raise state.failure
            if query.get('_id') == 'landsat_collections':
                return copy.deepcopy(state.mapping)
            if state.document and state.document['active']:
                return copy.deepcopy(state.document)
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            state.closed += 1
        def __getitem__(self, name):
            return Collection() if name == 'vis_params' else self

    monkeypatch.setattr(pymongo, 'MongoClient', Client)
    loader = importlib.import_module('app.visualization.vis_params_loader')
    monkeypatch.setattr(loader, 'USE_MONGODB_VIS_PARAMS', True)
    state.loader = loader
    state.worker = importlib.import_module('app.tasks.cache_operations')
    return state


@pytest.mark.asyncio
async def test_worker_uses_the_same_mongo_document_and_mapping_as_initialized_api(catalog, monkeypatch):
    module = importlib.import_module('app.visualization.vis_params_db')
    class Collection:
        def find(self, query):
            async def documents():
                yield copy.deepcopy(catalog.document)
            return documents()
        async def find_one(self, query):
            return copy.deepcopy(catalog.mapping)
    monkeypatch.setattr(module, 'get_database', lambda: SimpleNamespace(vis_params=Collection()))
    manager = module.VisParamsManager()
    await manager.get_all_vis_params()
    collection = manager.get_landsat_collection(2020)
    api_vis = await manager.get_landsat_vis_params('landsat-tvi-false', collection)
    image, worker_vis = catalog.worker._build_landsat_image(
        None, {'dtStart': '2020-01-01', 'dtEnd': '2020-04-30'}, 'landsat-tvi-false', 'BEST_IMAGE')
    assert collection == 'LANDSAT/CONFIGURADA'
    assert worker_vis == api_vis == {
        'bands': ['CUSTOM_1', 'CUSTOM_2', 'CUSTOM_3'],
        'min': '0.12,0.23,0.34', 'max': '0.7,0.8,0.9', 'gamma': '1.25',
    }
    assert catalog.closed == 1
    assert catalog.document['satellite_configs'][0]['vis_params']['min'] == [0.12, 0.23, 0.34]


@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_inactive_visualization_never_falls_back_to_hardcoded(catalog, missing):
    if missing:
        catalog.document = None
    else:
        catalog.document['active'] = False
    with pytest.raises(ValueError):
        catalog.worker._build_landsat_image(None, {'dtStart': '2020-01-01', 'dtEnd': '2020-04-30'},
                                           'landsat-tvi-false', 'BEST_IMAGE')
    assert catalog.closed == 1


def test_catalog_failure_is_propagated_and_closes_the_client(catalog):
    catalog.failure = pymongo.errors.ServerSelectionTimeoutError('catálogo indisponível')
    with pytest.raises(pymongo.errors.ServerSelectionTimeoutError):
        catalog.worker._build_landsat_image(None, {'dtStart': '2020-01-01', 'dtEnd': '2020-04-30'},
                                           'landsat-tvi-false', 'MOSAIC')
    assert catalog.closed == 1


def test_flag_selects_collection_source_without_changing_the_api_visual_source(catalog, monkeypatch):
    monkeypatch.setattr(catalog.loader, 'USE_MONGODB_VIS_PARAMS', False)
    monkeypatch.setattr(catalog.loader, 'get_landsat_collection', lambda year: 'LANDSAT/LC08/C02/T1_L2')
    _, vis = catalog.worker._build_landsat_image(None, {'dtStart': '2020-01-01', 'dtEnd': '2020-04-30'},
                                              'landsat-tvi-false', 'MOSAIC')
    assert vis['bands'] == ['CUSTOM_1', 'CUSTOM_2', 'CUSTOM_3']
    assert vis['min'] == '0.12,0.23,0.34'
    assert catalog.closed == 1
