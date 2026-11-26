import time
from datetime import datetime, timezone
from unittest.mock import Mock

from tests.unit.test_offline_campaign_store import store
from tests.unit.test_campaign_manifest import bdc_campaign


def prepared_bdc(store):
    store.db.campaign.replace_one({'_id': 'test'}, dict(bdc_campaign(), _id='test', zoomLevel=18))
    job = store.start('test')
    token = store.claim('test')
    for _ in range(10):
        store.prepare(job, token)
        job = store.jobs.find_one({'_id': 'test'})
        if job['status'] == 'running':
            return job, token
    raise AssertionError('Manifesto BDC não foi concluído')


def test_bdc_worker_routes_without_calling_gee_and_persists_expiry(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepared_bdc(store)
    gee = Mock(side_effect=AssertionError('BDC não pode chamar Earth Engine'))
    bdc = Mock(return_value={'status': 'cached', 'expires_at': time.time() + 100})
    process_window(store, job, token, gee, limit=1, bdc_generate=bdc)
    gee.assert_not_called()
    assert bdc.call_count == 1
    assert store.status('test')['counts']['completed'] == 1


def test_bdc_selection_pending_does_not_exhaust_attempts(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepared_bdc(store)
    pending = Mock(return_value={'status': 'deferred', 'reason': 'bdc_selection_pending', 'retry_after': 30})
    process_window(store, job, token, Mock(), limit=1, bdc_generate=pending)
    item = store.items.find_one({'state': 'retry_wait'})
    assert item['attempts'] == 0
    assert item['admission_deferrals'] == 1
    assert item['reason'] == 'bdc_selection_pending'


def test_bdc_verified_absence_is_distinct_from_failed_download(store):
    job, token = prepared_bdc(store)
    item = store.next_item(job, token)
    evidence = {'kind': 'bdc_selection_no_image', 'selection_id': 'a' * 64,
                'collection': 'landsat-2', 'count': 0, 'checked_at': datetime.now(timezone.utc),
                'year': item['params']['year'], 'period': item['params']['period']}
    store.complete_item(job, token, item, {'status': 'no_data', 'expires_at': time.time()+100, 'evidence': evidence})
    assert store.items.find_one({'_id': item['_id']})['state'] == 'no_data'
    item = store.next_item(job, token)
    store.complete_item(job, token, item, {'status': 'no_data', 'expires_at': time.time()+100})
    assert store.items.find_one({'_id': item['_id']})['state'] == 'retry_wait'


def test_bdc_worker_checks_revision_and_point_lock_before_render(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepared_bdc(store)
    bdc = Mock(side_effect=AssertionError('Não pode renderizar configuração alterada'))
    store.db.campaign.update_one({'_id': 'test'}, {'$set': {'bdcConfig.selectionRevision': 2}})
    process_window(store, job, token, Mock(), limit=1, bdc_generate=bdc)
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'stale'
    bdc.assert_not_called()


def test_bdc_worker_stops_when_point_is_in_maintenance(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepared_bdc(store)
    store.db.points.update_one({'_id': 'p'}, {'$set': {'pointMutationLock': 'operation'}})
    bdc = Mock(side_effect=AssertionError('Ponto em manutenção'))
    process_window(store, job, token, Mock(), limit=1, bdc_generate=bdc)
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'stale'
    bdc.assert_not_called()


def test_bdc_worker_discards_completion_if_point_changes_during_render(store):
    from app.tasks.offline_campaign import process_window
    job, token = prepared_bdc(store)
    def render(*args, **kwargs):
        store.db.points.update_one({'_id': 'p'}, {'$set': {'lon': -50}})
        return {'status': 'cached', 'expires_at': time.time()+100}
    process_window(store, job, token, Mock(), limit=1, bdc_generate=render)
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'stale'
    assert store.items.count_documents({'state': 'completed'}) == 0


def test_bdc_retry_persists_explicit_selection_retry_intent(store):
    job, token = prepared_bdc(store)
    store.items.update_many({'run_id': job['run_id']}, {'$set': {'state': 'blocked'}})
    store.finish(job, token)
    store.release('test', token)
    retried = store.start('test', mode='retry_failed')
    assert retried['mode'] == 'retry_failed'


def test_bdc_generator_warms_exact_selected_scene_and_regeneration_preserves_identity(monkeypatch):
    from app.modules.bdc import campaign_cache
    selected = {'state': 'ready', 'params': {'collection': 'landsat-2'},
                'result': {'best_local_id': 'local-scene', 'best_global_id': 'global-scene'}}
    resolver = Mock(return_value=selected)
    warmer = Mock(return_value={'status': 'cached', 'expires_at': time.time()+10})
    monkeypatch.setattr(campaign_cache, 'resolve_selection', resolver)
    monkeypatch.setattr(campaign_cache, 'warm_tile', warmer)
    params = {'year': 2020, 'period': 'WET', 'visparam': 'landsat-tvi-true', 'z': 18, 'x': 1, 'y': 2}
    result = campaign_cache.generate_tile('db', 'campaign', 'point', params, force=True, retry=True)
    assert result['status'] == 'cached'
    assert warmer.call_args.args[1:] == ('landsat-2', 'local-scene', 'landsat-tvi-true', 18, 1, 2)
    assert warmer.call_args.kwargs == {'force': True, 'admission_count': 4}
    assert warmer.call_args.args[0].background is True
    resolver.assert_called_once_with('db', 'campaign', 'point', 2020, 'WET', retry=True, retry_request=None)


def test_bdc_no_image_revalidates_fixed_reference_without_reselecting(monkeypatch):
    from app.modules.bdc import campaign_cache
    checked = datetime(2020, 1, 1, tzinfo=timezone.utc)
    selected = {'_id': 'a'*64, 'state': 'no_image', 'params': {'collection': 'landsat-2'},
                'createdAt': checked, 'updatedAt': checked}
    monkeypatch.setattr(campaign_cache, 'resolve_selection', Mock(return_value=selected))
    warmer = Mock(side_effect=AssertionError('Ausência não deve renderizar'))
    monkeypatch.setattr(campaign_cache, 'warm_tile', warmer)
    result = campaign_cache.generate_tile(None, None, None, {'year': 2020, 'period': 'WET'})
    assert result['expires_at'] > time.time() + 29*86400
    assert result['evidence']['reference_completed_at'] == checked
    assert result['evidence']['checked_at'] > checked
    assert result['evidence']['kind'] == 'bdc_selection_no_image'
    warmer.assert_not_called()



def test_bdc_accepts_same_initial_year_as_tvi():
    from app.cache.campaign_manifest import campaign_config
    campaign = bdc_campaign()
    campaign.update(initialYear=1970, finalYear=1984)
    assert campaign_config(campaign)['years'] == list(range(1970, 1985))



def test_bdc_renderer_change_invalidates_manifest_and_stops_job(store, monkeypatch):
    import importlib
    offline_tiles = importlib.import_module('app.modules.bdc.offline_tiles')
    from app.tasks.offline_campaign import process_window
    job, token = prepared_bdc(store)
    monkeypatch.setattr(offline_tiles, 'RENDER_VERSION', offline_tiles.RENDER_VERSION + 1)
    bdc = Mock(side_effect=AssertionError('Versão de renderização alterada'))
    process_window(store, job, token, Mock(), limit=1, bdc_generate=bdc)
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'stale'
    bdc.assert_not_called()


def test_bdc_dispatch_uses_authenticated_worker_for_initial_and_followup(monkeypatch):
    from app.tasks import offline_campaign
    task = Mock()
    monkeypatch.setattr(offline_campaign, 'run_offline_campaign', task)
    job = {'_id': 'test', 'config': {'source': 'bdc'}}
    offline_campaign.enqueue_offline_campaign(job)
    task.apply_async.assert_called_with(args=['test'], queue='bdc_offline', countdown=0)
    offline_campaign.enqueue_offline_campaign(job, countdown=5)
    task.apply_async.assert_called_with(args=['test'], queue='bdc_offline', countdown=5)
    task.delay.assert_not_called()
    offline_campaign.enqueue_offline_campaign({'_id': 'legacy', 'config': {}})
    task.delay.assert_called_once_with('legacy')


def test_recovery_preserves_bdc_worker_queue(store, monkeypatch):
    from contextlib import contextmanager
    from app.tasks import offline_campaign
    from datetime import timedelta
    job, token = prepared_bdc(store)
    store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_until': datetime.now(timezone.utc)-timedelta(seconds=1)}})
    @contextmanager
    def factory():
        yield store
    task = Mock()
    monkeypatch.setattr(offline_campaign, 'campaign_store', factory)
    monkeypatch.setattr(offline_campaign, 'run_offline_campaign', task)
    offline_campaign.recover_offline_campaigns.run()
    task.apply_async.assert_called_once_with(args=['test'], queue='bdc_offline', countdown=0)
    task.delay.assert_not_called()


def test_manifest_followup_is_immediate_bounded_and_releases_lease(store, monkeypatch):
    from contextlib import contextmanager
    from app.tasks import offline_campaign

    store.db.campaign.replace_one({'_id': 'test'}, dict(bdc_campaign(), _id='test', zoomLevel=18,
                                                       initialYear=1985, finalYear=2025))
    job = store.start('test')

    @contextmanager
    def factory():
        yield store

    dispatched = []
    def enqueue(current, countdown=None):
        assert store.jobs.find_one({'_id': 'test'}).get('lease_token') is None
        dispatched.append((current, countdown))

    monkeypatch.setattr(offline_campaign, 'campaign_store', factory)
    monkeypatch.setattr(offline_campaign, 'enqueue_offline_campaign', enqueue)
    result = offline_campaign.run_offline_campaign.run('test')
    assert result['status'] == 'preparing'
    assert store.items.count_documents({'run_id': job['run_id']}) == 250
    assert dispatched[0][1] == 0
    assert store.jobs.find_one({'_id': 'test'})['point_offset'] == 250
    offline_campaign.run_offline_campaign.run('test')
    assert store.items.count_documents({'run_id': job['run_id']}) == 500
    assert dispatched[1][1] == 0


def test_manifest_sealing_starts_rendering_immediately(store, monkeypatch):
    from contextlib import contextmanager
    from app.tasks import offline_campaign

    store.db.campaign.replace_one({'_id': 'test'}, dict(bdc_campaign(), _id='test', zoomLevel=18,
                                                       initialYear=2020, finalYear=2020))
    store.start('test')

    @contextmanager
    def factory():
        yield store

    dispatch = Mock()
    monkeypatch.setattr(offline_campaign, 'campaign_store', factory)
    monkeypatch.setattr(offline_campaign, 'enqueue_offline_campaign', dispatch)
    result = offline_campaign.run_offline_campaign.run('test')
    assert result['status'] == 'running'
    assert store.jobs.find_one({'_id': 'test'})['manifest_complete'] is True
    assert dispatch.call_args.kwargs['countdown'] == 0


def test_busy_campaign_does_not_prepare_or_dispatch_duplicate(store, monkeypatch):
    from contextlib import contextmanager
    from app.tasks import offline_campaign

    store.start('test')
    token = store.claim('test')

    @contextmanager
    def factory():
        yield store

    dispatch = Mock()
    monkeypatch.setattr(offline_campaign, 'campaign_store', factory)
    monkeypatch.setattr(offline_campaign, 'enqueue_offline_campaign', dispatch)
    assert offline_campaign.run_offline_campaign.run('test') == {'status': 'busy'}
    assert store.items.count_documents({}) == 0
    assert store.jobs.find_one({'_id': 'test'})['lease_token'] == token
    dispatch.assert_not_called()


def test_bdc_window_generates_tiles_concurrently_without_duplicate_claims(store, monkeypatch):
    from threading import Barrier, Lock
    from app.tasks import offline_campaign
    job, token = prepared_bdc(store)
    monkeypatch.setattr(offline_campaign, 'BDC_OFFLINE_PARALLELISM', 2, raising=False)
    gate = Barrier(2)
    seen = []
    lock = Lock()

    def render(db, campaign, point, params, **kwargs):
        with lock:
            seen.append((params['year'], params['period'], params['visparam'], params['z'], params['x'], params['y']))
        gate.wait(timeout=3)
        return {'status': 'cached', 'expires_at': time.time() + 100}

    offline_campaign.process_window(store, job, token, Mock(), limit=4, bdc_generate=render)
    assert len(seen) == len(set(seen)) == 4
    assert store.items.count_documents({'state': 'completed'}) == 4


def test_bdc_parallel_failure_does_not_discard_other_tile_results(store, monkeypatch):
    from threading import Barrier, Lock
    from app.tasks import offline_campaign
    job, token = prepared_bdc(store)
    monkeypatch.setattr(offline_campaign, 'BDC_OFFLINE_PARALLELISM', 2, raising=False)
    gate, lock, calls = Barrier(2), Lock(), []

    def render(*args, **kwargs):
        with lock:
            calls.append(1)
            fail = len(calls) == 1
        gate.wait(timeout=3)
        if fail:
            raise ValueError('Falha de renderização')
        return {'status': 'cached', 'expires_at': time.time() + 100}

    offline_campaign.process_window(store, job, token, Mock(), limit=2, bdc_generate=render)
    assert store.items.count_documents({'state': 'completed'}) == 1
    assert store.items.count_documents({'state': 'retry_wait', 'reason': 'ValueError'}) == 1


def test_bdc_parallel_window_stops_after_point_mutation(store, monkeypatch):
    from threading import Barrier
    from app.tasks import offline_campaign
    job, token = prepared_bdc(store)
    monkeypatch.setattr(offline_campaign, 'BDC_OFFLINE_PARALLELISM', 2, raising=False)
    gate, calls = Barrier(2), []

    def render(*args, **kwargs):
        calls.append(1)
        gate.wait(timeout=3)
        store.db.points.update_one({'_id': 'p'}, {'$set': {'pointMutationLock': 'mutation'}})
        return {'status': 'cached', 'expires_at': time.time() + 100}

    offline_campaign.process_window(store, job, token, Mock(), limit=8, bdc_generate=render)
    assert len(calls) == 2
    assert store.jobs.find_one({'_id': 'test'})['status'] == 'stale'
    assert store.items.count_documents({'state': 'completed'}) == 0


def test_bdc_parallel_window_waits_for_active_workers_after_lease_loss(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Barrier, Event, Lock
    from app.tasks import offline_campaign
    job, token = prepared_bdc(store)
    monkeypatch.setattr(offline_campaign, 'BDC_OFFLINE_PARALLELISM', 2, raising=False)
    gate, release, expired, lock, calls = Barrier(2), Event(), Event(), Lock(), []

    def render(*args, **kwargs):
        with lock:
            calls.append(1)
            first = len(calls) == 1
        gate.wait(timeout=3)
        if first:
            store.jobs.update_one({'_id': 'test'}, {'$set': {'lease_until': datetime.now(timezone.utc) - timedelta(seconds=1)}})
            expired.set()
        else:
            assert release.wait(3)
        return {'status': 'cached', 'expires_at': time.time() + 100}

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(offline_campaign.process_window, store, job, token, Mock(), 8, render)
        try:
            assert expired.wait(3)
            assert not pending.done()
        finally:
            release.set()
        pending.result(timeout=3)
    assert len(calls) == 2
    assert store.items.count_documents({'state': 'completed'}) == 0


def test_offline_parallelism_configuration_is_bounded(monkeypatch):
    import runpy
    from pathlib import Path
    import pytest
    path = Path(__file__).parents[2] / 'app/modules/bdc/background.py'
    monkeypatch.delenv('BDC_OFFLINE_PARALLELISM', raising=False)
    assert runpy.run_path(str(path))['BDC_OFFLINE_PARALLELISM'] == 4
    for value in ('1', '4', '8'):
        monkeypatch.setenv('BDC_OFFLINE_PARALLELISM', value)
        assert runpy.run_path(str(path))['BDC_OFFLINE_PARALLELISM'] == int(value)
    for value in ('', '0', '9', '-1', '2.5', 'true'):
        monkeypatch.setenv('BDC_OFFLINE_PARALLELISM', value)
        with pytest.raises(ValueError, match='BDC_OFFLINE_PARALLELISM'):
            runpy.run_path(str(path))


def test_bdc_render_followup_is_immediate_only_when_items_are_eligible(store, monkeypatch):
    from contextlib import contextmanager
    from datetime import timedelta
    from app.tasks import offline_campaign
    job, token = prepared_bdc(store)
    store.release('test', token)

    @contextmanager
    def factory():
        yield store

    def complete_batch(current_store, current_job, current_token, generate):
        for _ in range(8):
            item = current_store.next_item(current_job, current_token)
            if item is None:
                break
            current_store.complete_item(current_job, current_token, item,
                                        {'status': 'cached', 'expires_at': time.time() + 100})
        return current_store.finish(current_job, current_token)

    dispatch = Mock()
    monkeypatch.setattr(offline_campaign, 'campaign_store', factory)
    monkeypatch.setattr(offline_campaign, 'enqueue_offline_campaign', dispatch)
    monkeypatch.setattr(offline_campaign, 'process_window', complete_batch)
    offline_campaign.run_offline_campaign.run('test')
    assert store.items.count_documents({'state': 'completed'}) == 8
    assert dispatch.call_args.kwargs['countdown'] == 0
    store.items.update_many({'state': 'pending'}, {'$set': {'state': 'retry_wait',
                            'next_retry': datetime.now(timezone.utc) + timedelta(seconds=30)}})
    dispatch.reset_mock()
    offline_campaign.run_offline_campaign.run('test')
    assert dispatch.call_args.kwargs['countdown'] == 5


def test_default_bdc_window_is_bounded_to_32_tiles(store):
    from app.tasks import offline_campaign
    job, token = prepared_bdc(store)
    renderer = Mock(return_value={'status': 'cached', 'expires_at': time.time() + 100})
    offline_campaign.process_window(store, job, token, Mock(), bdc_generate=renderer)
    assert renderer.call_count == 32
    assert store.items.count_documents({'state': 'completed'}) == 32
