from datetime import datetime, timedelta, timezone

import mongomock
import pytest

from app.cache.offline_campaign import OfflineCampaignStore


def queue(source='bdc', pending=40, retries=16):
    store = OfflineCampaignStore(mongomock.MongoClient().tvi)
    now = datetime.now(timezone.utc)
    job = {'_id': 'campaign', 'run_id': 'run', 'config': {'source': source},
           'lease_token': 'worker', 'lease_until': now + timedelta(minutes=20)}
    store.jobs.insert_one(job)
    documents = [dict(_id=f'{state}-{i:03}', run_id='run', state=state, attempts=0,
                      next_retry=now - timedelta(hours=2 if state == 'pending' else 1))
                 for state, count in [('pending', pending), ('retry_wait', retries)] for i in range(count)]
    if documents:
        store.items.insert_many(documents)
    return store, job


def test_bdc_window_reserves_one_due_retry_for_every_three_pending_items():
    store, job = queue()
    items = [store.next_item(job, 'worker') for _ in range(32)]
    assert [item['_id'].split('-')[0] for item in items] == ['retry_wait', 'pending', 'pending', 'pending'] * 8
    assert len({item['_id'] for item in items}) == 32
    assert all(item['state'] == 'processing' and item['attempts'] == 1 and item['worker_token'] == 'worker' for item in items)


@pytest.mark.parametrize(('pending', 'retries'), [(0, 6), (6, 0), (0, 0)])
def test_bdc_falls_back_to_the_other_due_state_without_waiting(pending, retries):
    store, job = queue(pending=pending, retries=retries)
    assert all(store.next_item(job, 'worker') for _ in range(pending + retries))
    assert store.next_item(job, 'worker') is None


def test_bdc_future_retry_is_never_claimed_and_becomes_eligible_without_blocking_pending():
    store, job = queue(retries=1)
    store.items.update_one({'_id': 'retry_wait-000'}, {'$set': {'next_retry': datetime.now(timezone.utc) + timedelta(days=1)}})
    assert all(store.next_item(job, 'worker')['_id'].startswith('pending-') for _ in range(4))
    store.items.update_one({'_id': 'retry_wait-000'}, {'$set': {'next_retry': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert store.next_item(job, 'worker')['_id'] == 'retry_wait-000'


def test_each_bdc_worker_window_starts_with_a_retry_and_keeps_state_local_to_run():
    store, job = queue()
    assert store.next_item(job, 'worker')['_id'] == 'retry_wait-000'
    other = OfflineCampaignStore(store.db)
    assert other.next_item(job, 'worker')['_id'] == 'retry_wait-001'
    second = dict(job, _id='second', run_id='second-run')
    store.jobs.insert_one(second)
    store.items.insert_many([{'_id': 'second-' + state, 'run_id': 'second-run', 'state': state,
                             'next_retry': datetime.now(timezone.utc) - timedelta(hours=1), 'attempts': 0}
                            for state in ['pending', 'retry_wait']])
    assert store.next_item(second, 'worker')['_id'] == 'second-retry_wait'
    assert store.next_item(job, 'worker')['_id'].startswith('pending-')


def test_legacy_queue_keeps_chronological_order():
    store, job = queue(source='gee')
    assert all(store.next_item(job, 'worker')['_id'].startswith('pending-') for _ in range(32))


def test_expired_or_foreign_lease_cannot_consume_a_fairness_slot():
    store, job = queue()
    assert store.next_item(job, 'other-worker') is None
    store.jobs.update_one({'_id': job['_id']}, {'$set': {'lease_until': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert store.next_item(job, 'worker') is None
    assert store.items.count_documents({'state': 'processing'}) == 0
    store.jobs.update_one({'_id': job['_id']}, {'$set': {'lease_until': datetime.now(timezone.utc) + timedelta(minutes=1)}})
    assert store.next_item(job, 'worker')['_id'] == 'retry_wait-000'


def test_store_instances_do_not_reclaim_processing_items():
    store, job = queue()
    other = OfflineCampaignStore(store.db)
    identifiers = [current.next_item(job, 'worker')['_id'] for _ in range(24) for current in [store, other]]
    assert len(identifiers) == len(set(identifiers)) == 48
    assert store.items.count_documents({'state': 'processing', 'attempts': 1}) == 48
