import sys
from pathlib import Path
from importlib import import_module
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def api(monkeypatch):
    root = Path(__file__).resolve().parents[2] / 'app'
    for name, directory in [('app.core', 'core'), ('app.visualization', 'visualization')]:
        package = ModuleType(name)
        package.__path__ = [str(root / directory)]
        monkeypatch.setitem(sys.modules, name, package)
    config = ModuleType('app.core.config')
    config.logger = Mock()
    mongodb = ModuleType('app.core.mongodb')
    mongodb.get_database = Mock(side_effect=AssertionError('Acesso inesperado ao banco'))
    mongodb.get_users_collection = Mock(side_effect=AssertionError('Acesso inesperado à autenticação'))
    manager = ModuleType('app.visualization.vis_params_db')
    manager.vis_params_manager = SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'app.core.config', config)
    monkeypatch.setitem(sys.modules, 'app.core.mongodb', mongodb)
    monkeypatch.setitem(sys.modules, 'app.visualization.vis_params_db', manager)
    return import_module('app.api.vis_params')


@pytest.fixture
def client(api):
    vis_params = api
    app = FastAPI()
    app.include_router(vis_params.router)
    for dependency in vis_params.router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: True

    def selected_endpoint(name):
        async def endpoint(**kwargs):
            return {'endpoint': name}
        return endpoint

    for route in app.routes:
        if hasattr(route, 'dependant'):
            route.dependant.call = selected_endpoint(route.endpoint.__name__)
    return TestClient(app)


@pytest.mark.parametrize(('path', 'method', 'body', 'endpoint'), [
    ('landsat-collections', 'GET', None, 'get_landsat_collections'),
    ('landsat-collections', 'PUT', [], 'update_landsat_collections'),
    ('sentinel-collections', 'GET', None, 'get_sentinel_collections'),
    ('sentinel-collections', 'PUT', {}, 'update_sentinel_collections'),
    ('composicao-exemplo', 'GET', None, 'get_vis_param'),
    ('composicao-exemplo', 'PUT', {'display_name': 'Composição'}, 'update_vis_param'),
])
def test_colecoes_e_parametros_usam_o_endpoint_correspondente(client, path, method, body, endpoint):
    response = client.request(method, '/api/vis-params/' + path, json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {'endpoint': endpoint}


@pytest.mark.parametrize('path', ['landsat-collections', 'sentinel-collections', 'composicao-exemplo'])
def test_registro_preserva_autenticacao_obrigatoria(client, path):
    client.app.dependency_overrides.clear()
    response = client.get('/api/vis-params/' + path)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Basic'


@pytest.mark.parametrize(('query', 'expected'), [
    ('', ['landsat-exemplo', 'sentinel-exemplo']),
    ('?category=landsat&active=true&tag=floresta', ['landsat-exemplo']),
    ('?active=false', ['sentinel-exemplo']),
])
def test_listagem_exclui_configuracoes_de_colecoes_e_preserva_filtros(api, monkeypatch, query, expected):
    collection = mongomock.MongoClient().tvi.vis_params
    collection.insert_many([
        {'_id': 'landsat_collections', 'mappings': []},
        {'_id': 'sentinel_collections', 'collections': []},
        {'_id': 'landsat-exemplo', 'name': 'landsat-exemplo', 'category': 'landsat', 'active': True, 'tags': ['floresta']},
        {'_id': 'sentinel-exemplo', 'name': 'sentinel-exemplo', 'category': 'sentinel2', 'active': False, 'tags': []},
    ])

    class AsyncCollection:
        async def find(self, filters):
            for document in collection.find(filters):
                yield document

    monkeypatch.setattr(api, 'get_database', lambda: SimpleNamespace(vis_params=AsyncCollection()))
    app = FastAPI()
    app.include_router(api.router)
    for dependency in api.router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: True
    response = TestClient(app).get('/api/vis-params/' + query)
    assert response.status_code == 200
    assert [document['_id'] for document in response.json()] == expected
    assert all(document['name'] and document['category'] for document in response.json())
