from datetime import datetime, timedelta, timezone
import time

import mongomock
import pytest


@pytest.fixture
def store():
    from app.cache.offline_campaign import OfflineCampaignStore
    db = mongomock.MongoClient().tvi
    db.campaign.insert_one({'_id': 'test', 'imageType': 'landsat', 'useDynamicMaps': True,
                           'initialYear': 2020, 'finalYear': 2020, 'zoomLevel': 18,
                           'visParams': ['landsat-tvi-false']})
    db.points.insert_one({'_id': 'p', 'campaign': 'test', 'lat': -16, 'lon': -49, 'cached': True})
    return OfflineCampaignStore(db)


def prepare(store):
    job = store.start('test')
    token = store.claim('test')
    store.prepare(job, token)
    return store.jobs.find_one({'_id': 'test'}), token


def test_start_is_idempotent_and_does_not_trust_legacy_cached(store):
    first = store.start('test')
    assert store.start('test')['run_id'] == first['run_id']
    assert first['status'] == 'preparing'
    assert store.status('test')['counts']['completed'] == 0


def test_only_one_worker_claims_campaign_and_expired_lease_recovers(store):
    store.start('test')
    first = store.claim('test')
    assert first
    assert store.claim('test') is None
    store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_until': datetime.utcnow() - timedelta(seconds=1)}})
    second = store.claim('test')
    assert second and second != first
    assert not store.release('test', first)
    assert store.jobs.find_one({'_id': 'test'})['lease_token'] == second


def test_manifest_is_durable_and_preparation_replay_does_not_duplicate(store):
    job, token = prepare(store)
    count = store.items.count_documents({'run_id': job['run_id']})
    assert count > 2
    store.prepare(job, token)
    assert store.items.count_documents({'run_id': job['run_id']}) == count
    assert store.status('test')['counts']['completed'] == 0
    assert store.status('test')['status'] == 'running'


def test_gee_manifest_preparation_continues_without_google_delay(store, monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import Mock
    from app.tasks import offline_campaign
    store.db.points.insert_one({'_id': 'q', 'campaign': 'test', 'lat': -15, 'lon': -48})
    store.start('test')

    @contextmanager
    def factory():
        yield store

    dispatch = Mock()
    task_globals = offline_campaign.run_offline_campaign.run.__globals__
    monkeypatch.setitem(task_globals, 'campaign_store', factory)
    monkeypatch.setitem(task_globals, 'enqueue_offline_campaign', dispatch)
    result = offline_campaign.run_offline_campaign.run('test')
    assert result['status'] == 'preparing'
    assert dispatch.call_args.kwargs['countdown'] == 0


def test_error_exhaustion_is_not_absence_of_data_or_completion(store):
    from app.cache.offline_campaign import failure_state
    assert failure_state('invalid_tile', 5)['state'] == 'blocked'
    assert failure_state('gee_credential_denied', 1)['state'] == 'blocked'
    retry = failure_state('quota', 1, retry_after=300)
    assert retry['state'] == 'retry_wait'
    assert retry['delay'] >= 300


def test_restricted_project_remains_pending_without_exhausting_item(store):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.items.update_one({'_id': item['_id']}, {'$set': {'attempts': 5}})
    item['attempts'] = 5
    store.complete_item(job, token, item, {
        'status': 'deferred', 'reason': 'GoogleAdmissionError',
        'admission_reason': 'project_restricted', 'http_status': 429,
        'failure_phase': 'map_create', 'retry_after': 900,
    })
    saved = store.items.find_one({'_id': item['_id']})
    assert saved['state'] == 'retry_wait'
    assert saved['attempts'] == 4
    assert saved['expires_at'] is None
    assert saved['next_retry'] > datetime.utcnow() + timedelta(seconds=890)


def test_black_image_is_blocked_without_changing_other_failure_policies():
    from app.cache.offline_campaign import failure_state
    assert failure_state('invalid_tile', 1, validation_code='black_image') == {'state': 'blocked', 'delay': 0}
    for code in (None, 'white_image', 'transparent_image', 'corrupt_image', 'persistence_unconfirmed'):
        assert failure_state('invalid_tile', 1, validation_code=code)['state'] == 'retry_wait'
    for reason in ('TimeoutError', 'ConnectionError', 'EarthEngineRateLimitedError', 'GoogleAdmissionError'):
        assert failure_state(reason, 1, 300, validation_code='black_image') == {'state': 'retry_wait', 'delay': 300}
    assert failure_state('invalid_tile', 1, 300) == {'state': 'retry_wait', 'delay': 300}


@pytest.mark.parametrize('secondary', [
    {},
    {'secondary_error': 'TimeoutError', 'secondary_phase': 'absence_check'},
    {'admission_reason': 'rate', 'admission_phase': 'absence_check', 'retry_after': 300},
])
def test_black_image_blocks_first_attempt_and_preserves_secondary_failure(store, secondary):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.complete_item(job, token, item, {
        'status': 'failed', 'reason': 'invalid_tile', 'failure_phase': 'image_validation',
        'http_status': 200, 'diagnostics': {'validation_code': 'black_image'}, **secondary,
    })
    saved = store.items.find_one({'_id': item['_id']})
    assert saved['state'] == 'blocked'
    assert saved['attempts'] == 1
    assert saved['next_retry'] is None
    assert saved['reason'] == 'invalid_tile'
    assert saved['failure_phase'] == 'image_validation'
    assert saved['diagnostics']['validation_code'] == 'black_image'
    assert saved.get('secondary_error') == secondary.get('secondary_error')
    assert saved.get('admission_reason') == secondary.get('admission_reason')
    assert not saved.get('evidence')
    assert store.status('test')['counts']['no_data'] == 0
    store.items.delete_many({'_id': {'$ne': item['_id']}})
    assert store.next_item(job, token) is None


def test_completion_requires_all_item_results(store):
    job, token = prepare(store)
    assert store.finish(job, token)['status'] == 'running'
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'completed', 'expires_at': time.time() + 3600}})
    result = store.finish(job, token)
    assert result['status'] == 'completed'
    result = store.status('test')
    assert result['counts']['pending'] == 0
    assert result['counts']['completed'] == result['total']


def test_retry_failed_preserves_valid_items(store):
    job, token = prepare(store)
    first = store.items.find_one({'run_id': job['run_id']})
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'blocked', 'attempts': 5}})
    store.items.update_one({'_id': first['_id']}, {'$set': {'state': 'completed', 'expires_at': time.time() + 3600}})
    store.finish(job, token)
    store.release('test', token)
    retried = store.start('test', mode='retry_failed')
    assert retried['run_id'] == job['run_id']
    assert store.items.find_one({'_id': first['_id']})['state'] == 'completed'
    assert store.items.count_documents({'state': 'pending'}) > 0


def test_stale_worker_cannot_publish_success_after_new_generation(store):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_token': 'new-owner'}})
    assert not store.complete_item(job, token, item, {'status': 'cached'})
    assert store.items.find_one({'_id': item['_id']})['state'] != 'completed'


def test_configuration_change_invalidates_completed_status(store):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'completed', 'expires_at': time.time() + 3600}})
    store.finish(job, token)
    store.db.campaign.update_one({'_id': 'test'}, {'$set': {'finalYear': 2021}})
    assert store.status('test')['configuration_changed'] is True
    assert store.status('test')['status'] != 'completed'


def test_manifest_reports_unique_tiles_for_overlapping_points(store):
    store.db.points.insert_one({'_id': 'q', 'campaign': 'test', 'lat': -16, 'lon': -49})
    job = store.start('test')
    token = store.claim('test')
    store.prepare(job, token)
    store.prepare(store.jobs.find_one({'_id': 'test'}), token)
    summary = store.status('test')
    assert summary['unique_tiles'] * 2 == summary['total']


@pytest.mark.parametrize('diagnostics', [{}, {'validation_code': 'black_image'}])
def test_justified_absence_is_terminal_but_generic_missing_is_not(store, diagnostics):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.complete_item(job, token, item, {'status': 'no_data'})
    assert store.items.find_one({'_id': item['_id']})['state'] == 'retry_wait'
    item = store.next_item(job, token)
    evidence = {'kind': 'empty_collection', 'count': 0, 'collection': 'LANDSAT/LC08/C02/T1_L2',
                'period': item['params']['period'], 'year': item['params']['year'],
                'checked_at': datetime.utcnow().replace(microsecond=0)}
    store.complete_item(job, token, item, {'status': 'no_data', 'evidence': evidence,
                                         'diagnostics': diagnostics, 'expires_at': time.time() + 3600})
    assert store.items.find_one({'_id': item['_id']})['state'] == 'no_data'
    assert store.items.find_one({'_id': item['_id']})['evidence'] == evidence


def test_retry_intent_survives_failure_before_resetting_items(store, monkeypatch):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'blocked', 'attempts': 5}})
    store.finish(job, token)
    store.release('test', token)
    update_many = store.items.update_many

    def interrupted(query, update, **kwargs):
        if query.get('state') == {'$in': ['blocked', 'retry_wait']}:
            raise ConnectionError('isolated interruption')
        return update_many(query, update, **kwargs)

    monkeypatch.setattr(store.items, 'update_many', interrupted)
    with pytest.raises(ConnectionError):
        store.start('test', mode='retry_failed')
    monkeypatch.setattr(store.items, 'update_many', update_many)
    store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_until': datetime.utcnow() - timedelta(seconds=1)}})
    recovered_token = store.claim('test')
    recovered_job = store.jobs.find_one({'_id': 'test'})
    assert recovered_token
    assert store.next_item(recovered_job, recovered_token) is not None
    assert store.items.count_documents({'state': 'blocked'}) == 0


def test_incomplete_manifest_cannot_be_retried_as_completed_coverage(store):
    job = store.start('test')
    store.jobs.update_one({'_id': 'test'}, {'$set': {'status': 'blocked'}})
    with pytest.raises(ValueError, match='cobertura'):
        store.start('test', mode='retry_failed')
    assert store.jobs.find_one({'_id': 'test'})['run_id'] == job['run_id']


def test_retry_rejects_changed_point_count(store):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'blocked', 'attempts': 5}})
    store.finish(job, token)
    store.release('test', token)
    store.db.points.insert_one({'_id': 'q', 'campaign': 'test', 'lat': -15, 'lon': -48})
    with pytest.raises(ValueError, match='configuração'):
        store.start('test', mode='retry_failed')


@pytest.mark.parametrize('expiry', [None, 0, float('nan'), float('inf')])
def test_success_without_valid_expiry_is_not_completed(store, expiry):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.complete_item(job, token, item, {'status': 'cached', 'expires_at': expiry})
    assert store.items.find_one({'_id': item['_id']})['state'] == 'retry_wait'


def test_completion_preserves_verified_expiry(store):
    job, token = prepare(store)
    item = store.next_item(job, token)
    expiry = time.time() + 3600
    store.complete_item(job, token, item, {'status': 'cached', 'expires_at': expiry})
    saved = store.items.find_one({'_id': item['_id']})
    assert saved['expires_at'] == expiry
    assert saved['state'] == 'completed'


def test_finish_requeues_expired_images(store):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']},
                            {'$set': {'state': 'completed', 'expires_at': time.time() - 1}})
    assert store.finish(job, token)['status'] == 'running'
    assert store.next_item(job, token) is not None


def test_read_status_does_not_report_expired_campaign_as_completed(store):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']},
                            {'$set': {'state': 'completed', 'expires_at': time.time() + 3600}})
    assert store.finish(job, token)['status'] == 'completed'
    store.release('test', token)
    first = store.items.find_one({'run_id': job['run_id']})
    store.items.update_one({'_id': first['_id']}, {'$set': {'expires_at': time.time() - 1}})
    summary = store.status('test')
    assert summary['status'] == 'blocked'
    assert summary['percent'] < 100
    assert summary['errors'][0]['reason'] == 'cache_expired'
    assert store.items.find_one({'_id': first['_id']})['state'] == 'completed'
    store.start('test', mode='retry_failed')
    assert store.items.find_one({'_id': first['_id']})['state'] == 'pending'


@pytest.mark.parametrize('replacement', [False, True])
def test_point_changes_with_same_count_invalidate_coverage(store, replacement):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'blocked'}})
    store.finish(job, token)
    store.release('test', token)
    if replacement:
        store.db.points.delete_one({'_id': 'p'})
        store.db.points.insert_one({'_id': 'q', 'campaign': 'test', 'lat': -16, 'lon': -49})
    else:
        store.db.points.update_one({'_id': 'p'}, {'$set': {'lat': -3, 'lon': -60}})
    assert store.status('test')['status'] == 'stale'
    with pytest.raises(ValueError, match='configuração'):
        store.start('test', mode='retry_failed')


def test_preparation_is_bounded_and_resumes_inside_point(store):
    from app.cache.campaign_manifest import manifest_for_point
    job = store.start('test')
    token = store.claim('test')
    expected = list(manifest_for_point(store.db.points.find_one({'_id': 'p'}), job['config']))
    store.prepare(job, token, limit=7)
    assert store.items.count_documents({'run_id': job['run_id']}) == 7
    store.release('test', token)
    token = store.claim('test')
    for _ in range(len(expected)):
        current = store.jobs.find_one({'_id': 'test'})
        if current['status'] != 'preparing':
            break
        store.prepare(current, token, limit=7)
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'running'
    assert store.items.count_documents({'run_id': job['run_id']}) == len(expected)
    assert store.jobs.find_one({'_id': 'test'})['prepared_points'] == 1


def test_preparation_detects_coordinate_change_between_windows(store):
    job = store.start('test')
    token = store.claim('test')
    store.prepare(job, token, limit=7)
    store.db.points.update_one({'_id': 'p'}, {'$set': {'lat': -3}})
    store.prepare(store.jobs.find_one({'_id': 'test'}), token, limit=7)
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'stale'


def test_worker_finish_does_not_aggregate_all_items(store, monkeypatch):
    job, token = prepare(store)
    def forbidden(*args, **kwargs):
        raise AssertionError('O worker não deve agregar o manifesto em cada janela')
    monkeypatch.setattr(store.items, 'aggregate', forbidden)
    assert store.finish(job, token)['status'] == 'running'


def test_cached_progress_reuses_summary_until_refresh_interval(store, monkeypatch):
    prepare(store)
    initial = store.status('test', cached=True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Consultas repetidas devem reutilizar o resumo recente')
    monkeypatch.setattr(store.items, 'aggregate', forbidden)
    assert store.status('test', cached=True)['counts'] == initial['counts']


def test_cached_progress_never_outlives_image_expiry(store):
    job, token = prepare(store)
    expiry = time.time() + 10
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'completed', 'expires_at': expiry}})
    store.finish(job, token)
    store.status('test', cached=True)
    assert store.jobs.find_one({'_id': 'test'})['progress_summary']['valid_until'] <= expiry


def test_expired_absence_is_rechecked_before_completion(store):
    job, token = prepare(store)
    store.items.update_many({'run_id': job['run_id']},
                            {'$set': {'state': 'no_data', 'expires_at': time.time() - 1}})
    assert store.status('test')['counts']['no_data'] == 0
    assert store.finish(job, token)['status'] == 'running'
    assert store.next_item(job, token) is not None


def test_admission_deferrals_preserve_failure_budget_and_retry_deadline(store):
    job, token = prepare(store)
    first = store.items.find_one({'run_id': job['run_id']})
    store.items.delete_many({'_id': {'$ne': first['_id']}})
    for attempt in range(7):
        store.items.update_one({'_id': first['_id']}, {'$set': {'next_retry': datetime.now(timezone.utc)}})
        item = store.next_item(job, token)
        assert store.complete_item(job, token, item, {
            'status': 'deferred', 'reason': 'GoogleAdmissionError', 'retry_after': 180,
            'failure_phase': 'download', 'admission_reason': 'concurrency',
        })
        saved = store.items.find_one({'_id': item['_id']})
        assert saved['state'] == 'retry_wait'
        assert saved['attempts'] == 0
        assert saved['admission_deferrals'] == attempt + 1
        assert saved['next_retry'].replace(tzinfo=timezone.utc) >= datetime.now(timezone.utc) + timedelta(seconds=179)
        assert saved['admission_reason'] == 'concurrency'
        assert saved['failure_phase'] == 'download'


def test_real_validation_failure_still_exhausts_budget_after_deferral(store):
    job, token = prepare(store)
    first = store.items.find_one({'run_id': job['run_id']})
    store.items.delete_many({'_id': {'$ne': first['_id']}})
    store.complete_item(job, token, store.next_item(job, token), {
        'status': 'deferred', 'reason': 'GoogleAdmissionError',
        'failure_phase': 'download', 'admission_reason': 'rate',
    })
    for attempt in range(5):
        store.items.update_one({'_id': first['_id']}, {'$set': {'next_retry': datetime.now(timezone.utc)}})
        item = store.next_item(job, token)
        assert store.complete_item(job, token, item, {
            'status': 'failed', 'reason': 'invalid_tile', 'failure_phase': 'image_validation',
            'diagnostics': {'validation_code': 'transparent_image', 'byte_length': 222},
        })
    saved = store.items.find_one({'_id': first['_id']})
    assert saved['state'] == 'blocked'
    assert saved['attempts'] == 5
    assert saved['diagnostics']['validation_code'] == 'transparent_image'


def test_deferral_cannot_compensate_twice_or_after_lease_loss(store):
    job, token = prepare(store)
    result = {'status': 'deferred', 'reason': 'GoogleAdmissionError',
              'failure_phase': 'map_create', 'admission_reason': 'rate'}
    first = store.next_item(job, token)
    assert store.complete_item(job, token, first, result)
    assert not store.complete_item(job, token, first, result)
    assert store.items.find_one({'_id': first['_id']})['attempts'] == 0
    second = store.next_item(job, token)
    store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_token': 'other'}})
    assert not store.complete_item(job, token, second, result)
    assert store.items.find_one({'_id': second['_id']})['attempts'] == 1


@pytest.mark.parametrize('changes', [
    {'http_status': 429}, {'failure_phase': 'absence_check'}, {'reason': 'invalid_tile'},
])
def test_ambiguous_or_source_failure_cannot_receive_free_retry(store, changes):
    job, token = prepare(store)
    item = store.next_item(job, token)
    result = dict(status='deferred', reason='GoogleAdmissionError',
                  failure_phase='download', admission_reason='coordinator_unavailable')
    result.update(changes)
    store.complete_item(job, token, item, result)
    saved = store.items.find_one({'_id': item['_id']})
    assert saved['attempts'] == 1
    assert saved.get('admission_deferrals', 0) == 0


@pytest.mark.parametrize('retry_after', [float('nan'), float('inf'), float('-inf'), None, 'invalid'])
def test_deferred_retry_deadline_rejects_non_finite_values(store, retry_after):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.complete_item(job, token, item, {
        'status': 'deferred', 'reason': 'GoogleAdmissionError', 'failure_phase': 'download',
        'admission_reason': 'rate', 'retry_after': retry_after,
    })
    saved = store.items.find_one({'_id': item['_id']})
    delay = (saved['next_retry'].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    assert 29 <= delay <= 30
    assert saved['attempts'] == 0


@pytest.mark.parametrize(('status', 'reason'), [('failed', 'TimeoutError'), ('deferred', 'GoogleAdmissionError')])
def test_transport_failure_consumes_attempt_without_fabricating_http_status(store, status, reason):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.complete_item(job, token, item, {
        'status': status, 'reason': reason, 'transport_error': 'TimeoutError',
        'http_status': None, 'failure_phase': 'map_create', 'admission_reason': 'rate', 'retry_after': 1,
    })
    saved = store.items.find_one({'_id': item['_id']})
    assert saved['attempts'] == 1
    assert saved['state'] == 'retry_wait'
    assert saved['reason'] == reason
    assert saved['transport_error'] == 'TimeoutError'
    assert saved['http_status'] is None
    assert saved.get('admission_deferrals', 0) == 0


def test_policy_upgrade_preserves_old_items_and_rejects_late_completion(store):
    job, token = prepare(store)
    item = store.next_item(job, token)
    store.jobs.update_one({'_id': 'test'}, {'$set': {'config.version': 1}})
    old = store.jobs.find_one({'_id': 'test'})
    store.mark_stale(old, token)
    store.release('test', token)
    current = store.start('test')
    assert current['run_id'] != old['run_id']
    assert current['config']['version'] == 2
    assert current['previous_run']['run_id'] == old['run_id']
    assert store.items.count_documents({'run_id': old['run_id']}) > 0
    assert not store.owned(old, token)
    store.complete_item(old, token, item, {'status': 'cached', 'expires_at': time.time() + 3600})
    assert store.items.find_one({'_id': item['_id']})['state'] == 'processing'
    assert store.start('test')['run_id'] == current['run_id']


def test_point_summary_tracks_partial_complete_expired_and_absence(store):
    job, token = prepare(store)
    first = store.items.find_one({'run_id': job['run_id']})
    assert store.status('test')['point_counts'] == dict(complete=0, partial=0, pending=1, no_data=0)
    store.items.update_one({'_id': first['_id']}, {'$set': {'state': 'completed', 'expires_at': time.time() + 600}})
    assert store.status('test')['point_counts']['partial'] == 1
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'completed', 'expires_at': time.time() + 600}})
    assert store.status('test')['point_counts']['complete'] == 1
    store.items.update_one({'_id': first['_id']}, {'$set': {'expires_at': time.time() - 1}})
    assert store.status('test')['point_counts']['partial'] == 1
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'no_data', 'expires_at': time.time() + 600}})
    assert store.status('test')['point_counts']['no_data'] == 1
    store.db.points.update_one({'_id': 'p'}, {'$set': {'lon': -48}})
    assert store.status('test')['point_counts'] == dict(complete=0, partial=0, pending=1, no_data=0)


def test_point_counts_persist_with_bounded_freshness(store):
    job, token = prepare(store)
    store.status('test', cached=True)
    summary = store.jobs.find_one({'_id': 'test'})['progress_summary']
    assert summary['points']['pending'] == 1
    assert summary['valid_until'] <= time.time() + 30
