import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


DATES = {'dtStart': '2020-06-01', 'dtEnd': '2020-10-30'}
VIS = {'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': '0.05,0.05,0.03',
       'max': '0.6,0.55,0.3', 'gamma': '1.2'}
COLLECTION = 'LANDSAT/CONFIGURADA'


def value_tree(value):
    if isinstance(value, Expression):
        return value.tree
    if isinstance(value, (tuple, list)):
        return [value_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: value_tree(item) for key, item in value.items()}
    return value


class Expression:
    def __init__(self, operation, *arguments):
        self.tree = [operation, *[value_tree(argument) for argument in arguments]]

    def __getattr__(self, name):
        def call(*arguments):
            if name == 'map' and callable(arguments[0]):
                return Expression(name, self, arguments[0](Expression('item')))
            return Expression(name, self, *arguments)
        return call


def configure_expression_recorder(monkeypatch):
    layers = importlib.import_module('app.api.layers')
    worker = importlib.import_module('app.tasks.cache_operations')
    hardcoded = importlib.import_module('app.visualization.visParam')
    ee = layers.ee
    class ImageFactory:
        def __new__(cls, image):
            return image if isinstance(image, Expression) else Expression('Image', image)
        constant = staticmethod(lambda values: Expression('constant', values))
    monkeypatch.setattr(ee, 'Image', ImageFactory)
    monkeypatch.setattr(ee, 'ImageCollection', lambda name: Expression('ImageCollection', name))
    monkeypatch.setattr(ee, 'List', lambda values: Expression('List', values))
    monkeypatch.setattr(ee, 'Filter', SimpleNamespace(
        lt=lambda *args: Expression('Filter.lt', *args),
        eq=lambda *args: Expression('Filter.eq', *args),
        inList=lambda *args: Expression('Filter.inList', *args),
    ))
    monkeypatch.setattr(ee, 'Reducer', SimpleNamespace(min=lambda: Expression('Reducer.min')))
    monkeypatch.setattr(ee, 'Algorithms', SimpleNamespace(If=lambda *args: Expression('If', *args)))
    monkeypatch.setattr(layers, 'get_landsat_collection', lambda year: COLLECTION)
    monkeypatch.setattr(hardcoded, 'get_landsat_collection', lambda year: COLLECTION)
    monkeypatch.setattr(hardcoded, 'get_landsat_vis_params', lambda *args: copy.deepcopy(VIS))
    monkeypatch.setattr(worker, 'resolve_landsat_config_sync',
                        lambda name, year: (COLLECTION, copy.deepcopy(VIS)))
    monkeypatch.setattr(layers, 'call_gee', lambda function, *args, **kwargs: function(*args))
    monkeypatch.setattr(worker, 'call_gee', lambda function, *args, **kwargs: function(*args))
    captured = []
    def get_map(parameters):
        captured.append(value_tree(parameters))
        return {'tile_fetcher': SimpleNamespace(url_format='https://example.test/map')}
    monkeypatch.setattr(ee.data, 'getMapId', get_map)
    return layers, worker, captured


@pytest.fixture
def expression_recorder(monkeypatch):
    return configure_expression_recorder(monkeypatch)


@pytest.mark.parametrize('mode', ['BEST_IMAGE', 'MOSAIC'])
def test_api_preserves_the_published_landsat_expression(expression_recorder, mode):
    layers, _, captured = expression_recorder
    layers._create_landsat_layer_with_params('geometry', DATES, copy.deepcopy(VIS), mode)
    expected = json.loads((Path(__file__).parent / 'fixtures' / f'landsat_{mode.lower()}.json').read_text())
    assert [parameters['image'] for parameters in captured] == [expected]


@pytest.mark.parametrize('mode', ['BEST_IMAGE', 'MOSAIC'])
def test_worker_and_api_build_identical_landsat_expressions(expression_recorder, mode):
    layers, worker, captured = expression_recorder
    layers._create_landsat_layer_with_params('geometry', DATES, copy.deepcopy(VIS), mode)
    image, vis = worker._build_landsat_image('geometry', DATES, 'landsat-tvi-false', mode)
    assert image.tree == captured[0]['image']
    assert vis == VIS


def operations(tree, name):
    if not isinstance(tree, list):
        return []
    found = [tree] if tree and tree[0] == name else []
    return found + [node for child in tree for node in operations(child, name)]


def test_best_image_checks_missing_bands_and_keeps_transparent_multiband_fallback(expression_recorder):
    _, worker, _ = expression_recorder
    image, _ = worker._build_landsat_image('geometry', DATES, 'landsat-tvi-false', 'BEST_IMAGE')
    tree = image.tree
    assert ['Filter.eq', 'has_required_bands', 1] in operations(tree, 'Filter.eq')
    assert any(node[-2:] == ['CLOUD_COVER', False] for node in operations(tree, 'sort'))
    assert any(node == ['updateMask', ['rename', ['constant', [0, 0, 0]], VIS['bands']], 0]
               for node in operations(tree, 'updateMask'))
    assert operations(tree, 'mosaic')


def test_mosaic_masks_cloud_and_shadow_and_preserves_scaling(expression_recorder):
    _, worker, _ = expression_recorder
    image, _ = worker._build_landsat_image('geometry', DATES, 'landsat-tvi-false', 'MOSAIC')
    tree = image.tree
    assert {node[-1] for node in operations(tree, 'bitwiseAnd')} == {8, 16}
    assert any(node[-1] == 0.0000275 for node in operations(tree, 'multiply'))
    assert any(node[-1] == -0.2 for node in operations(tree, 'add'))
    assert tree[0] == 'If'
    assert tree[-1] == ['updateMask', ['rename', ['constant', [0, 0, 0]], VIS['bands']], 0]


@pytest.mark.parametrize('mode', [None, ''])
def test_empty_composite_mode_matches_the_api_default(expression_recorder, mode):
    layers, worker, captured = expression_recorder
    layers._create_landsat_layer_with_params('geometry', DATES, copy.deepcopy(VIS), 'BEST_IMAGE')
    image, _ = worker._build_landsat_image('geometry', DATES, 'landsat-tvi-false', mode)
    assert image.tree == captured[0]['image']


@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('campaign', [None, 'campaign'])
def test_worker_preserves_api_visual_payload_without_mutating_catalog(expression_recorder, monkeypatch, indexed, campaign):
    layers, worker, captured = expression_recorder
    vis = {'bands': ['SR_B5', 'SR_B4'], 'min': [-0.2], 'max': [0.9], 'gamma': [1.2]}
    if indexed:
        vis.update({'palette': ['#a52a2a', '#006837'], 'index': {
            'type': 'normalized_difference', 'bands': ['SR_B5', 'SR_B4'], 'name': 'NDVI'}})
    original = copy.deepcopy(vis)
    hardcoded = importlib.import_module('app.visualization.visParam')
    monkeypatch.setattr(hardcoded, 'get_landsat_vis_params', lambda *args: vis)
    monkeypatch.setattr(worker, 'resolve_landsat_config_sync', lambda *args: (COLLECTION, vis))
    monkeypatch.setattr(worker, 'get_campaign_token', lambda campaign: {'project_id': 'project'})
    def credential_map(image, parameters, credential, **kwargs):
        captured.append({'image': image.tree, **copy.deepcopy(parameters)})
        return 'https://example.test/map'
    monkeypatch.setattr(worker, 'create_map_with_credential', credential_map)
    layers._create_landsat_layer_with_params('geometry', DATES, vis, 'MOSAIC')
    worker._warm_create_landsat_url('geometry', DATES, 'landsat-tvi-false', 'MOSAIC', campaign)
    assert captured[0] == captured[1]
    assert vis == original
