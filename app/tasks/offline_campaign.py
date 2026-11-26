import time
from contextlib import contextmanager
from datetime import datetime, timezone

from pymongo import MongoClient

from app.cache.offline_campaign import ACTIVE, OfflineCampaignStore
from app.core.config import settings
from app.tasks.celery_app import celery_app


@contextmanager
def campaign_store():
    with MongoClient(settings.get('MONGODB_URL', 'mongodb://localhost:27017'),
                     serverSelectionTimeoutMS=5000, socketTimeoutMS=15000) as client:
        yield OfflineCampaignStore(client[settings.get('MONGODB_DB', 'tvi')])


def source_absence_evidence(params):
    import ee
    from app.core.gee_admission import call_gee
    from app.services.tile import tile2goehashBBOX
    from app.tasks.cache_operations import _build_period_dates, resolve_landsat_config_sync

    _, bbox = tile2goehashBBOX(params['x'], params['y'], params['z'])
    dates = _build_period_dates(params['period'], params['year'], params['month'])
    geom = ee.Geometry.BBox(bbox['w'], bbox['s'], bbox['e'], bbox['n'])
    if params['layer'] == 'landsat':
        collection, _ = resolve_landsat_config_sync(params['visparam'], params['year'])
    else:
        collection = 'COPERNICUS/S2_HARMONIZED'
    images = ee.ImageCollection(collection).filterDate(dates['dtStart'], dates['dtEnd']).filterBounds(geom)
    if params['layer'] == 'landsat' and params['composite_mode'] == 'BEST_IMAGE':
        images = images.filter(ee.Filter.lt('CLOUD_COVER', 40))
    count = call_gee(images.size().getInfo, operation='sdk', background=True)
    if type(count) is int and count == 0:
        return {'kind': 'empty_collection', 'count': count, 'collection': collection,
                'period': params['period'], 'year': params['year'], 'dates': dates,
                'bbox': bbox, 'checked_at': datetime.now(timezone.utc),
                'criteria': 'CLOUD_COVER < 40' if params['layer'] == 'landsat' else 'date_and_bounds'}
    return None


def process_window(store, job, token, generate, limit=8, bdc_generate=None):
    started = time.monotonic()
    is_bdc = job['config'].get('source') == 'bdc'
    if is_bdc and bdc_generate is None:
        from app.modules.bdc.campaign_cache import generate_tile
        bdc_generate = generate_tile
    for _ in range(limit):
        if time.monotonic() - started >= 180:
            break
        item = store.next_item(job, token)
        if not item:
            break
        if is_bdc and store.configuration_changed(job, check_points=False):
            store.mark_stale(job, token)
            break
        point = store.db.points.find_one({'_id': item.get('point_ref'), 'campaign': job['_id']})
        from app.cache.campaign_manifest import fingerprint
        if point is None or fingerprint([type(point['_id']).__name__, str(point['_id']), point.get('lat'), point.get('lon')]) != item.get('point_fingerprint'):
            store.mark_stale(job, token)
            break
        if is_bdc and point.get('pointMutationLock'):
            store.mark_stale(job, token)
            break
        try:
            shared = store.items.find_one({'run_id': job['run_id'], 'key': item['key'], 'state': 'completed'}, {'_id': 1})
            if is_bdc:
                campaign = store.db.campaign.find_one({'_id': job['_id']})
                result = bdc_generate(store.db, campaign, point, item['params'],
                                      force=job['mode'] == 'regenerate' and not shared,
                                      retry=job['mode'] in {'retry_failed', 'regenerate'},
                                      retry_request=job.get('retry_request', job['run_id']))
                current = store.db.points.find_one({'_id': point['_id'], 'campaign': job['_id']})
                if (current is None or current.get('pointMutationLock')
                        or current.get('lat') != point['lat'] or current.get('lon') != point['lon']
                        or store.configuration_changed(job, check_points=False)):
                    store.mark_stale(job, token)
                    break
            else:
                result = generate(**item['params'], validate_image=True,
                                  force=job['mode'] == 'regenerate' and not shared)
        except Exception as exc:
            result = {'status': 'failed', 'reason': type(exc).__name__,
                      'retry_after': getattr(exc, 'retry_after', 0)}
        if not store.complete_item(job, token, item, result):
            break
    return store.finish(job, token)


def enqueue_offline_campaign(job, countdown=None):
    if job.get('config', {}).get('source') == 'bdc':
        return run_offline_campaign.apply_async(args=[job['_id']], queue='bdc', countdown=countdown or 0)
    if countdown is not None:
        return run_offline_campaign.apply_async(args=[job['_id']], countdown=countdown)
    return run_offline_campaign.delay(job['_id'])


@celery_app.task(queue='low_priority', acks_late=True, reject_on_worker_lost=True)
def run_offline_campaign(campaign_id):
    from app.tasks.cache_operations import cache_warm_tile

    with campaign_store() as store:
        token = store.claim(campaign_id)
        if token is None:
            return {'status': 'busy'}
        job = store.jobs.find_one({'_id': campaign_id})
        try:
            if store.configuration_changed(job, check_points=False):
                store.jobs.update_one({'_id': campaign_id, 'lease_token': token}, {'$set': {'status': 'stale'}})
            elif job['status'] == 'preparing':
                store.prepare(job, token)
            else:
                process_window(store, job, token, cache_warm_tile.run)
        except ValueError:
            store.jobs.update_one({'_id': campaign_id, 'lease_token': token},
                                   {'$set': {'status': 'blocked', 'error': 'Dados da campanha inválidos'}})
            raise
        finally:
            store.release(campaign_id, token)
        status = store.jobs.find_one({'_id': campaign_id})
        if status['status'] in ACTIVE:
            enqueue_offline_campaign(status, countdown=5)
        return {'status': status['status'], 'total': status.get('total', 0)}


@celery_app.task(queue='low_priority')
def recover_offline_campaigns():
    with campaign_store() as store:
        jobs = store.jobs.find({'status': {'$in': ACTIVE}, 'lease_until': {'$lte': datetime.now(timezone.utc)}},
                               {'_id': 1, 'config.source': 1}).sort('updated_at', 1).limit(20)
        for job in jobs:
            enqueue_offline_campaign(job)


celery_app.conf.beat_schedule['offline-campaign-recovery'] = {
    'task': 'app.tasks.offline_campaign.recover_offline_campaigns', 'schedule': 30.0,
}
