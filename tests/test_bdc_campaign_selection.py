import importlib
import json
import shutil
import subprocess
from pathlib import Path

import mongomock
import pytest

@pytest.fixture
def context(monkeypatch):
    global selection
    selection = importlib.import_module('app.modules.bdc.campaign_selection')
    db = mongomock.MongoClient().tvi
    campaign = {'_id': 'campanha', 'imagerySource': 'bdc', 'initialYear': 2020, 'finalYear': 2024,
                'bdcConfig': {'collection': 'landsat-2', 'selectionRevision': 1, 'bufferRadiusM': 1000,
                              'selectionStrategy': 'local_cloud', 'profiles': ['natural']}}
    point = {'_id': '1_campanha', 'campaign': 'campanha', 'lon': -49.25, 'lat': -16.5}
    db.points.insert_one(point)
    calls = []
    monkeypatch.setattr(selection, 'enqueue_selection', calls.append)
    return db, campaign, point, calls


def terminal(db, campaign, point, state='ready'):
    saved = selection.identity(campaign, point, 2020, 'WET')
    rows = [{'id': 'scene-original', 'eligible': True}] if state == 'ready' else []
    saved.update(state=state, result={'complete': True, 'rows': rows, 'evaluated': len(rows),
                                    'eligible': len(rows), 'best_local_id': rows[0]['id'] if rows else None,
                                    'best_global_id': None})
    db.campaignImageSelections.insert_one(saved)
    return saved


@pytest.mark.parametrize('lon,expected', [
    (-49.25, 'f567e26bf213d01c1ddf07d826f10267ee713436bd745ff6e998c65232bdfa4f'),
    (-49.0, 'c589767dc8e7850eb685e0602e885adb229dd042e344fc984839fb1540e36d87'),
    (1e-7, '6113bf6b528e3839348e71b3dc95206415ea95aa4a282759d33f3d438a8aa025'),
    (1e-6, '2516e56624fbcc2f5d863f418c976ac9703c8b8c4ae91c8103206addb5bacbfa'),
    (-0.0, 'a86b77078de38fea8f7d4004dea0404278c166a89ad9ae52809b9d3149396d0a'),
    (0.0000123456789, '64512f86e3959b06f1f86771ae9929b66e5c8d3b861a3617f26ff1e66fc1e4b8'),
])
def test_identity_matches_javascript(context, lon, expected):
    _, campaign, point, _ = context
    point['lon'] = lon
    campaign['bdcConfig']['bufferRadiusM'] = 1000.0
    campaign['bdcConfig']['selectionRevision'] = 1.0
    assert selection.identity(campaign, point, 2020, 'WET')['_id'] == expected
    path = Path(__file__).resolve().parents[2] / 'tvi/src/server/services/bdcSelectionService.js'
    if path.exists() and shutil.which('node'):
        actual = subprocess.check_output(['node', '-e',
            'const x=JSON.parse(process.argv[1]); console.log(require(process.argv[2]).identity(...x)._id)',
            json.dumps([campaign, point, 2020, 'WET']), str(path)], text=True).strip()
        assert actual == expected


@pytest.mark.parametrize('state', ['ready', 'no_image'])
def test_historical_reference_never_reselects(context, state):
    db, campaign, point, calls = context
    original = terminal(db, campaign, point, state)
    resolved = selection.resolve_selection(db, campaign, point, 2020, 'WET', retry=True)
    assert resolved['result'] == original['result']
    assert db.bdc_selection_jobs.count_documents({}) == 0
    assert calls == []


def test_pending_reuses_job_and_reserves_only_one_dispatch(context):
    db, campaign, point, calls = context
    first = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    second = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert first['state'] == second['state'] == 'pending'
    assert first['jobId'] == second['jobId']
    assert calls == [first['jobId']]
    assert db.campaignImageSelections.count_documents({}) == 1


def test_ready_job_preserves_local_scene(context):
    db, campaign, point, _ = context
    first = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    result = {'complete': True, 'rows': [{'id': 'local', 'eligible': True}, {'id': 'global', 'eligible': True}],
              'evaluated': 2, 'eligible': 2, 'best_local_id': 'local', 'best_global_id': 'global'}
    db.bdc_selection_jobs.update_one({'_id': first['jobId']}, {'$set': {'state': 'ready', 'result': result}})
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET')['result'] == result


def test_invalid_historical_result_is_not_reselected(context):
    db, campaign, point, calls = context
    terminal(db, campaign, point)
    db.campaignImageSelections.update_one({}, {'$set': {'result.complete': False}})
    with pytest.raises(selection.InvalidSelectionResult):
        selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert calls == []


@pytest.mark.parametrize('removed', [False, True])
def test_point_guard(context, removed):
    db, campaign, point, calls = context
    terminal(db, campaign, point)
    if removed:
        db.points.delete_many({})
    else:
        db.points.update_one({}, {'$set': {'pointMutationLock': 'operation'}})
    with pytest.raises(selection.PointUnavailable) as error:
        selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert error.value.status == (404 if removed else 409)
    assert calls == []


def test_concurrent_terminal_update_wins(context, monkeypatch):
    db, campaign, point, _ = context
    first = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    store_get = selection.SelectionJobStore.get
    result = {'complete': True, 'rows': [], 'evaluated': 0, 'eligible': 0,
              'best_local_id': None, 'best_global_id': None}
    def raced(store, job_id):
        remote = store_get(store, job_id)
        db.campaignImageSelections.update_one({'_id': first['_id']},
            {'$set': {'state': 'no_image', 'result': result}, '$inc': {'updateVersion': 1}})
        return remote
    monkeypatch.setattr(selection.SelectionJobStore, 'get', raced)
    resolved = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert resolved['state'] == 'no_image'
    assert resolved['result'] == result


def test_deletion_during_remote_resolution_cannot_resurrect_reference(context, monkeypatch):
    db, campaign, point, _ = context
    selection.resolve_selection(db, campaign, point, 2020, 'WET')
    original = selection.SelectionJobStore.get
    def deleted(store, job_id):
        remote = original(store, job_id)
        db.points.delete_many({})
        db.campaignImageSelections.delete_many({})
        return remote
    monkeypatch.setattr(selection.SelectionJobStore, 'get', deleted)
    with pytest.raises(selection.PointUnavailable):
        selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert db.campaignImageSelections.count_documents({}) == 0


def test_retry_only_explicit_retryable_failure(context):
    db, campaign, point, calls = context
    first = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    db.bdc_selection_jobs.update_one({'_id': first['jobId']},
                                   {'$set': {'state': 'failed', 'error': {'retryable': True}}})
    failed = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert failed['state'] == 'failed'
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET')['state'] == 'failed'
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET', retry=True)['state'] == 'pending'
    assert db.bdc_selection_jobs.find_one({})['retry_epoch'] == 1


def test_queue_failure_releases_dispatch_reservation(context, monkeypatch):
    db, campaign, point, calls = context
    def unavailable(job_id):
        raise RuntimeError('Fila indisponível')
    monkeypatch.setattr(selection, 'enqueue_selection', unavailable)
    with pytest.raises(selection.SelectionQueueUnavailable):
        selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert 'dispatch_until' not in db.bdc_selection_jobs.find_one({})
    monkeypatch.setattr(selection, 'enqueue_selection', calls.append)
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET')['state'] == 'pending'
    assert len(calls) == 1


@pytest.mark.parametrize('coordinate', ['lon', 'lat'])
def test_changed_coordinates_reject_stale_snapshot(context, coordinate):
    db, campaign, point, calls = context
    db.points.update_one({}, {'$set': {coordinate: point[coordinate] + 0.1}})
    with pytest.raises(selection.PointUnavailable) as error:
        selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert error.value.status == 409
    assert db.campaignImageSelections.count_documents({}) == 0
    assert calls == []


def test_point_move_during_remote_resolution_keeps_saved_reference_unchanged(context, monkeypatch):
    db, campaign, point, calls = context
    first = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    previous_calls = list(calls)
    original = selection.SelectionJobStore.get
    def moved(store, job_id):
        remote = original(store, job_id)
        db.points.update_one({}, {'$set': {'lon': point['lon'] + 0.1}})
        return remote
    monkeypatch.setattr(selection.SelectionJobStore, 'get', moved)
    with pytest.raises(selection.PointUnavailable) as error:
        selection.resolve_selection(db, campaign, point, 2020, 'WET')
    assert error.value.status == 409
    assert db.campaignImageSelections.find_one({}) == first
    assert calls == previous_calls


def test_offline_retry_is_once_per_run_even_if_remote_fails_again(context):
    db, campaign, point, calls = context
    first = selection.resolve_selection(db, campaign, point, 2020, 'WET')
    def fail():
        error = {'code': 'source_unavailable', 'retryable': True}
        db.bdc_selection_jobs.update_one({'_id': first['jobId']}, {'$set': {'state': 'failed', 'error': error}})
        db.campaignImageSelections.update_one({'_id': first['_id']}, {'$set': {'state': 'failed', 'error': error}})
    fail()
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET', retry=True, retry_request='run-a')['state'] == 'pending'
    fail()
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET', retry=True, retry_request='run-a')['state'] == 'failed'
    assert db.bdc_selection_jobs.find_one({'_id': first['jobId']})['retry_epoch'] == 1
    assert selection.resolve_selection(db, campaign, point, 2020, 'WET', retry=True, retry_request='run-b')['state'] == 'pending'
    assert db.bdc_selection_jobs.find_one({'_id': first['jobId']})['retry_epoch'] == 2
