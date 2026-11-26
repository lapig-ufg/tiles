import uuid

import pytest
from celery import Celery

import app.api.cache as cache_api


@pytest.fixture
def backend(monkeypatch):
    app = Celery('warm-status-' + uuid.uuid4().hex, broker='memory://', backend='cache+memory://')
    monkeypatch.setattr(cache_api, 'celery_app', app)
    yield app
    app.close()


@pytest.mark.asyncio
async def test_polling_dispatcher_follows_coordinator_progress(backend):
    dispatcher = uuid.uuid4().hex
    job_id = 'warm-' + dispatcher
    backend.backend.store_result(dispatcher, {'status': 'warming_started', 'job_id': job_id}, 'SUCCESS')
    progress = {'status': 'warming', 'processed': 2, 'total_tiles': 4, 'failed': 1}
    backend.backend.store_result(job_id, progress, 'PROGRESS')
    result = await cache_api.get_task_status(dispatcher)
    assert result.data['task_id'] == dispatcher
    assert result.data['resolved_task_id'] == job_id
    assert result.data['state'] == 'PROGRESS'
    assert result.data['ready'] is False
    assert result.data['successful'] is None
    assert result.data['result'] == progress


@pytest.mark.asyncio
async def test_polling_preserves_errors_from_completed_windows(backend):
    dispatcher = uuid.uuid4().hex
    job_id = 'warm-' + dispatcher
    backend.backend.store_result(dispatcher, {'status': 'warming_started', 'job_id': job_id}, 'SUCCESS')
    summary = {'status': 'completed_with_errors', 'processed': 4, 'total_tiles': 4,
               'cached': 3, 'failed': 1, 'errors': [{'tile': 'tile-0', 'reason': 'cache_warm_failed'}]}
    backend.backend.store_result(job_id, summary, 'SUCCESS')
    result = await cache_api.get_task_status(dispatcher)
    assert result.data['ready'] is True
    assert result.data['result'] == summary


@pytest.mark.asyncio
async def test_polling_other_tasks_keeps_original_contract(backend):
    task_id = uuid.uuid4().hex
    payload = {'status': 'scheduled', 'job_id': 'legacy-group'}
    backend.backend.store_result(task_id, payload, 'SUCCESS')
    result = await cache_api.get_task_status(task_id)
    assert result.data['task_id'] == task_id
    assert result.data['state'] == 'SUCCESS'
    assert result.data['result'] == payload
