from datetime import datetime, timedelta, timezone
import mongomock
import pytest
from app.cache.offline_campaign import OfflineCampaignStore


@pytest.mark.parametrize('source', ['bdc', 'gee'])
def test_dispatch_finishes_earliest_point_and_year_before_advancing(source):
    store = OfflineCampaignStore(mongomock.MongoClient().tvi)
    now = datetime.now(timezone.utc)
    job = {'_id': 'c', 'run_id': 'r', 'dispatch_policy': 'point-year-v1',
           'config': {'source': source}, 'lease_token': 't', 'lease_until': now + timedelta(minutes=20)}
    store.jobs.insert_one(job)
    for key, point, year in [('a', 10, 2014), ('b', 2, 2015), ('c', 2, 2014), ('d', 2, 2014)]:
        store.items.insert_one({'_id': key, 'run_id': 'r', 'point_order': point, 'point_id': str(point),
            'params': {'year': year, 'period': 'WET'}, 'state': 'pending', 'attempts': 0, 'next_retry': now})
    assert store.next_item(job, 't')['_id'] == 'c'
    assert store.next_item(job, 't')['_id'] == 'd'
    assert store.next_item(job, 't') is None
    store.items.update_many({'state': 'processing'}, {'$set': {'state': 'completed'}})
    assert store.next_item(job, 't')['_id'] == 'b'
    store.items.update_one({'_id': 'b'}, {'$set': {'state': 'retry_wait', 'next_retry': now + timedelta(hours=1)}})
    assert store.next_item(job, 't') is None
    assert not store.has_ready_item(job)
    store.items.update_one({'_id': 'b'}, {'$set': {'state': 'blocked'}})
    assert store.next_item(job, 't')['_id'] == 'a'


def test_preparation_records_numeric_point_index_and_fixed_period_order():
    store = OfflineCampaignStore(mongomock.MongoClient().tvi)
    store.db.campaign.insert_one({'_id': 'c', 'imageType': 'landsat', 'useDynamicMaps': True,
        'initialYear': 2014, 'finalYear': 2015, 'zoomLevel': 15, 'visParams': ['landsat-tvi-false']})
    store.db.points.insert_many([
        {'_id': '10_c', 'index': 10, 'campaign': 'c', 'lat': -16, 'lon': -49},
        {'_id': '2_c', 'index': 2, 'campaign': 'c', 'lat': -16, 'lon': -49}])
    job = store.start('c')
    token = store.claim('c')
    for _ in range(20):
        store.prepare(job, token)
        if store.jobs.find_one({'_id': 'c'})['status'] == 'running':
            break
    first = store.next_item(job, token)
    assert first['point_id'] == '2_c'
    assert first['point_order'] == 2
    assert first['params']['year'] == 2014
    assert first['params']['period'] == 'WET'
    assert store.next_item(job, 'foreign') is None
