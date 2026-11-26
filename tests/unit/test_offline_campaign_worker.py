import time

from tests.unit.test_offline_campaign_store import store, prepare


def test_worker_records_success_only_after_generator_returns(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepare(store)
    observed = []
    def generate(**kwargs):
        observed.append(store.status('test')['counts']['completed'])
        assert kwargs['validate_image'] is True
        assert kwargs['force'] is False
        return {'status': 'cached', 'expires_at': time.time() + 3600}
    process_window(store, job, token, generate, limit=2)
    assert observed == [0, 1]
    assert store.status('test')['counts']['completed'] == 2


def test_worker_persists_temporary_failure_and_continues_other_items(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepare(store)
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError('source')
        return {'status': 'cached', 'expires_at': time.time() + 3600}
    process_window(store, job, token, generate, limit=2)
    summary = store.status('test')
    assert summary['counts']['retry_wait'] == 1
    assert summary['counts']['completed'] == 1
    assert summary['errors'][0]['next_retry']
    assert summary['errors'][0]['attempts'] == 1


def test_worker_does_not_call_source_when_lease_is_lost(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepare(store)
    store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_token': 'another'}})
    calls = []
    process_window(store, job, token, lambda **kw: calls.append(kw), limit=2)
    assert calls == []


def test_invalid_image_without_persisted_absence_remains_retryable(store):
    from app.tasks import offline_campaign
    job, token = prepare(store)
    offline_campaign.process_window(store, job, token,
                                    lambda **kw: {'status': 'failed', 'reason': 'invalid_tile'}, limit=1)
    assert store.status('test')['counts']['no_data'] == 0
    assert store.status('test')['counts']['retry_wait'] == 1


def test_worker_stops_before_google_when_point_moves(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepare(store)
    store.db.points.update_one({'_id': 'p'}, {'$set': {'lat': -3, 'lon': -60}})
    calls = []
    assert process_window(store, job, token, lambda **kw: calls.append(kw))['status'] == 'stale'
    assert calls == []
