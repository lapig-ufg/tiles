from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.unit.test_offline_campaign_store import store


@pytest.fixture
def client(store, monkeypatch):
    from app.api import offline_cache
    @contextmanager
    def session():
        yield store
    monkeypatch.setattr(offline_cache, 'campaign_store', session)
    monkeypatch.setattr(offline_cache.run_offline_campaign, 'delay', lambda *args: None)
    app = FastAPI()
    app.include_router(offline_cache.router)
    for dependency in offline_cache.router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: True
    return TestClient(app)


def test_status_does_not_start_generation(client, store):
    response = client.get('/api/cache/campaign/test/offline')
    assert response.status_code == 200
    assert response.json()['status'] == 'not_started'
    assert store.jobs.count_documents({}) == 0


def test_start_validates_input_and_has_durable_status(client):
    response = client.post('/api/cache/campaign/test/offline', json={'mode': 'start'})
    assert response.status_code == 200
    assert client.get('/api/cache/campaign/test/offline').json()['status'] == 'preparing'
    assert client.post('/api/cache/campaign/test/offline', json={'mode': 'clear_all'}).status_code == 422
    assert client.post('/api/cache/campaign/missing/offline', json={}).status_code == 404


def test_pagination_is_bounded(client):
    assert client.get('/api/cache/campaign/test/offline?page=-1').status_code == 422
    assert client.get('/api/cache/campaign/test/offline?limit=10000').status_code == 422
