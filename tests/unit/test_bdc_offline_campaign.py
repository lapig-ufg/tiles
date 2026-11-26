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
    assert warmer.call_args.kwargs == {'force': True}
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
    task.apply_async.assert_called_with(args=['test'], queue='bdc', countdown=0)
    offline_campaign.enqueue_offline_campaign(job, countdown=5)
    task.apply_async.assert_called_with(args=['test'], queue='bdc', countdown=5)
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
    task.apply_async.assert_called_once_with(args=['test'], queue='bdc', countdown=0)
    task.delay.assert_not_called()
