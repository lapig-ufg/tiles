import math
import re
import hashlib
import time
import uuid
from itertools import islice
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.cache.campaign_manifest import campaign_config, fingerprint, manifest_for_point

ACTIVE = ('preparing', 'running')
STATES = ('pending', 'processing', 'completed', 'retry_wait', 'blocked', 'no_data')
LEASE_SECONDS = 1200
MAX_ATTEMPTS = 5


def failure_state(reason, attempts, retry_after=0, *, validation_code=None):
    if (reason in {'gee_credential_denied', 'bdc_selection_failed'} or attempts >= MAX_ATTEMPTS
            or reason == 'invalid_tile' and validation_code == 'black_image'):
        return {'state': 'blocked', 'delay': 0}
    try:
        retry_after = float(retry_after)
    except (TypeError, ValueError):
        retry_after = 0
    if not math.isfinite(retry_after):
        retry_after = 0
    return {'state': 'retry_wait', 'delay': max(30 * 2 ** min(attempts - 1, 7), retry_after, 30)}


class OfflineCampaignStore:
    def __init__(self, database):
        self.db = database
        self.jobs = database.offline_cache_jobs
        self.items = database.offline_cache_items
        self._bdc_dispatches = {}
        self.items.create_index([('run_id', 1), ('state', 1), ('next_retry', 1), ('_id', 1)])
        self.items.create_index([('run_id', 1), ('state', 1), ('_id', 1)])
        self.items.create_index([('run_id', 1), ('key', 1)])
        self.items.create_index([('run_id', 1), ('state', 1), ('expires_at', 1)])
        self.items.create_index([('run_id', 1), ('state', 1), ('point_order', 1), ('point_id', 1),
                                 ('params.year', 1), ('period_order', 1), ('_id', 1)])
        self.jobs.create_index([('status', 1), ('lease_until', 1)])

    def point_signature(self, campaign_id):
        digest = hashlib.sha256()
        count = 0
        for point in self.db.points.find({'campaign': campaign_id}, {'_id': 1, 'lat': 1, 'lon': 1}).sort('_id', 1).batch_size(1000):
            digest.update(fingerprint([type(point['_id']).__name__, str(point['_id']),
                                       point.get('lat'), point.get('lon')]).encode())
            count += 1
        return count, digest.hexdigest()

    def configuration_changed(self, job, check_points=True):
        campaign = self.db.campaign.find_one({'_id': job['_id']})
        try:
            if campaign_config(campaign or {}, job['config']['width'], job['config']['height']) != job['config']:
                return True
            return check_points and self.point_signature(job['_id']) != (job['point_count'], job.get('points_fingerprint'))
        except ValueError:
            return True

    def mark_stale(self, job, token):
        self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'lease_token': token},
                             {'$set': {'status': 'stale', 'updated_at': datetime.now(timezone.utc)}})

    def start(self, campaign_id, mode='start', width=512, height=512):
        if mode not in {'start', 'retry_failed', 'regenerate'}:
            raise ValueError('Operação de cache inválida')
        campaign = self.db.campaign.find_one({'_id': campaign_id})
        if campaign is None:
            raise LookupError('Campanha não encontrada')
        config = campaign_config(campaign, width, height)
        count, points_fingerprint = self.point_signature(campaign_id)
        if not count:
            raise ValueError('A campanha não possui pontos')
        previous = self.jobs.find_one({'_id': campaign_id})
        if previous and previous['status'] in ACTIVE:
            return previous
        if mode == 'retry_failed':
            if not previous:
                raise ValueError('A campanha ainda não possui execução offline')
            if (previous['config'] != config or previous['point_count'] != count
                    or previous.get('points_fingerprint') != points_fingerprint):
                raise ValueError('A configuração mudou; inicie uma execução com a cobertura atual')
            if not previous.get('manifest_complete'):
                raise ValueError('A cobertura está incompleta; gere ou verifique o cache novamente')
            token = uuid.uuid4().hex
            claimed = self.jobs.find_one_and_update(
                {'_id': campaign_id, 'run_id': previous['run_id'], 'status': previous['status'],
                 '$or': [{'lease_token': {'$exists': False}},
                         {'lease_until': {'$lte': datetime.now(timezone.utc)}}, {'lease_until': {'$exists': False}}]},
                {'$set': {'status': 'running', 'mode': mode, 'retry_request': uuid.uuid4().hex, 'retry_reset_pending': True, 'lease_token': token,
                          'lease_until': datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)}},
                return_document=ReturnDocument.AFTER)
            if claimed is None:
                return self.jobs.find_one({'_id': campaign_id})
            try:
                self.reset_failures(claimed, token)
            finally:
                self.release(campaign_id, token)
            return self.jobs.find_one({'_id': campaign_id})
        job = {'_id': campaign_id, 'run_id': uuid.uuid4().hex, 'status': 'preparing',
               'config': config, 'dispatch_policy': 'point-year-v1', 'mode': mode, 'point_count': count, 'prepared_points': 0,
               'points_fingerprint': points_fingerprint,
               'created_at': datetime.now(timezone.utc), 'updated_at': datetime.now(timezone.utc),
               'lease_until': datetime.now(timezone.utc), 'cursor': None}
        if previous:
            job['previous_run'] = {key: previous[key] for key in
                                   ('run_id', 'status', 'config', 'total', 'created_at', 'updated_at')
                                   if key in previous}
            result = self.jobs.replace_one({'_id': campaign_id, 'run_id': previous['run_id'],
                                            'status': previous['status'],
                                            '$or': [{'lease_token': {'$exists': False}},
                                                    {'lease_until': {'$lte': datetime.now(timezone.utc)}}]}, job)
            if not result.modified_count:
                return self.jobs.find_one({'_id': campaign_id})
        else:
            try:
                self.jobs.insert_one(job)
            except DuplicateKeyError:
                return self.jobs.find_one({'_id': campaign_id})
        return job

    def claim(self, campaign_id):
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        job = self.jobs.find_one_and_update(
            {'_id': campaign_id, 'status': {'$in': ACTIVE},
             '$or': [{'lease_token': {'$exists': False}}, {'lease_until': {'$lte': now}}]},
            {'$set': {'lease_token': token, 'lease_until': now + timedelta(seconds=LEASE_SECONDS)}},
            return_document=ReturnDocument.AFTER)
        if not job:
            return None
        if job.get('retry_reset_pending'):
            self.reset_failures(job, token)
        self.items.update_many({'run_id': job['run_id'], 'state': 'processing'},
                               {'$set': {'state': 'pending'}})
        self.requeue_expired(job, token)
        return token

    def expired_query(self, job, now):
        return {'run_id': job['run_id'], 'state': {'$in': ['completed', 'no_data']},
                '$nor': [{'expires_at': {'$gt': now, '$lt': float('inf')}}]}

    def requeue_expired(self, job, token):
        if not self.owned(job, token):
            return
        self.items.update_many(self.expired_query(job, time.time()),
                               {'$set': {'state': 'pending', 'attempts': 0,
                                         'reason': 'cache_expired', 'completed_at': None,
                                         'next_retry': datetime.now(timezone.utc)}})

    def reset_failures(self, job, token):
        if not self.owned(job, token):
            raise RuntimeError('Concessão do cache offline expirada')
        self.items.update_many({'run_id': job['run_id'], 'state': {'$in': ['blocked', 'retry_wait']}},
                               {'$set': {'state': 'pending', 'attempts': 0,
                                         'next_retry': datetime.now(timezone.utc)}})
        self.requeue_expired(job, token)
        self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'lease_token': token},
                             {'$unset': {'retry_reset_pending': '', 'error': ''},
                              '$set': {'updated_at': datetime.now(timezone.utc)}})

    def owned(self, job, token):
        return self.jobs.count_documents({'_id': job['_id'], 'run_id': job['run_id'],
                                         'lease_token': token, 'lease_until': {'$gt': datetime.now(timezone.utc)}}) == 1

    def release(self, campaign_id, token):
        return bool(self.jobs.update_one({'_id': campaign_id, 'lease_token': token},
                    {'$set': {'lease_until': datetime.now(timezone.utc)}, '$unset': {'lease_token': ''}}).modified_count)

    def seal_manifest(self, job, token):
        if not self.owned(job, token):
            return
        if self.configuration_changed(job):
            self.mark_stale(job, token)
            return
        unique = list(self.items.aggregate([
            {'$match': {'run_id': job['run_id']}}, {'$group': {'_id': '$key'}},
            {'$count': 'total'},
        ], allowDiskUse=True))
        self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'lease_token': token},
                             {'$set': {'status': 'running', 'manifest_complete': True,
                                       'total': self.items.count_documents({'run_id': job['run_id']}),
                                       'unique_tiles': unique[0]['total'] if unique else 0,
                                       'updated_at': datetime.now(timezone.utc)}})

    def prepare(self, job, token, limit=250):
        job = self.jobs.find_one({'_id': job['_id'], 'run_id': job['run_id']})
        if not job:
            return
        if job['status'] != 'preparing' or not self.owned(job, token):
            return
        query = {'campaign': job['_id']}
        if job.get('cursor') is not None:
            query['_id'] = {'$gt': job['cursor']}
        points = list(self.db.points.find(query, {'_id': 1, 'lat': 1, 'lon': 1, 'index': 1}).sort('_id', 1).limit(1))
        if not points:
            self.seal_manifest(job, token)
            return
        point = points[0]
        point_digest = fingerprint([type(point['_id']).__name__, str(point['_id']), point.get('lat'), point.get('lon')])
        if job.get('preparing_point') is not None and (job['preparing_point'] != point['_id'] or job.get('preparing_fingerprint') != point_digest):
            self.mark_stale(job, token)
            return
        offset = job.get('point_offset', 0)
        batch = list(islice(manifest_for_point(point, job['config'], start=offset), limit + 1))
        more = len(batch) > limit
        batch = batch[:limit]
        point_order = point.get('index')
        if type(point_order) is not int or point_order < 0:
            prefix = re.match(r'^([0-9]+)(?:_|$)', str(point['_id']))
            point_order = int(prefix.group(1)) if prefix else 9223372036854775807
        point_order = min(point_order, 9223372036854775807)
        documents = [dict(item, point_order=point_order,
                          period_order=0 if item['params']['period'] == 'WET' else 1, _id=fingerprint([job['run_id'], item['point_id'], item['key']]),
                          run_id=job['run_id'], state='pending', attempts=0,
                          next_retry=datetime.now(timezone.utc)) for item in batch]
        if not self.owned(job, token):
            return
        if documents:
            try:
                self.items.insert_many(documents, ordered=False)
            except BulkWriteError as exc:
                if exc.details.get('writeConcernErrors') or any(error['code'] != 11000 for error in exc.details.get('writeErrors', [])):
                    raise
        if not self.owned(job, token):
            return
        if more:
            self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'lease_token': token},
                                 {'$set': {'preparing_point': point['_id'], 'preparing_fingerprint': point_digest,
                                           'point_offset': offset + len(batch), 'updated_at': datetime.now(timezone.utc)}})
            return
        self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'lease_token': token},
                             {'$set': {'cursor': point['_id'], 'updated_at': datetime.now(timezone.utc)},
                              '$unset': {'preparing_point': '', 'preparing_fingerprint': '', 'point_offset': ''},
                              '$inc': {'prepared_points': 1}})
        if self.db.points.count_documents({'campaign': job['_id'], '_id': {'$gt': point['_id']}}) == 0:
            self.seal_manifest(job, token)

    def next_item(self, job, token):
        if not self.owned(job, token):
            return None
        if job.get('dispatch_policy') == 'point-year-v1':
            return self.next_ordered_item(job, token)
        bdc = job.get('config', {}).get('source') == 'bdc'
        dispatch = self._bdc_dispatches.get(job['run_id'], 0)
        states = ['retry_wait', 'pending'] if dispatch % 4 == 0 else ['pending', 'retry_wait']
        if not bdc:
            states = [{'$in': ['pending', 'retry_wait']}]
        now = datetime.now(timezone.utc)
        for state in states:
            item = self.items.find_one_and_update(
                {'run_id': job['run_id'], 'state': state, 'next_retry': {'$lte': now}},
                {'$set': {'state': 'processing', 'worker_token': token, 'started_at': now},
                 '$inc': {'attempts': 1}}, return_document=ReturnDocument.AFTER,
                sort=[('next_retry', 1), ('_id', 1)])
            if item is not None:
                if bdc:
                    self._bdc_dispatches[job['run_id']] = (dispatch + 1) % 4
                return item
        return None

    def ordered_head(self, job):
        order = [('point_order', 1), ('point_id', 1), ('params.year', 1), ('period_order', 1), ('_id', 1)]
        return self.items.find_one(
            {'run_id': job['run_id'], 'state': {'$in': ['pending', 'processing', 'retry_wait']}},
            sort=order)

    def has_ready_item(self, job):
        head = self.ordered_head(job)
        if head is None:
            return False
        return self.items.find_one({'run_id': job['run_id'], 'point_id': head['point_id'],
            'params.year': head['params']['year'], 'state': {'$in': ['pending', 'retry_wait']},
            'next_retry': {'$lte': datetime.now(timezone.utc)}}, {'_id': 1}) is not None

    def next_ordered_item(self, job, token):
        order = [('point_order', 1), ('point_id', 1), ('params.year', 1), ('period_order', 1), ('_id', 1)]
        head = self.ordered_head(job)
        if head is None:
            return None
        now = datetime.now(timezone.utc)
        return self.items.find_one_and_update(
            {'run_id': job['run_id'], 'point_order': head['point_order'],
             'point_id': head['point_id'], 'params.year': head['params']['year'],
             'state': {'$in': ['pending', 'retry_wait']}, 'next_retry': {'$lte': now}},
            {'$set': {'state': 'processing', 'worker_token': token, 'started_at': now},
             '$inc': {'attempts': 1}}, sort=order, return_document=ReturnDocument.AFTER)

    def complete_item(self, job, token, item, result):
        if not self.owned(job, token):
            return False
        successful = result.get('status') in {'cached', 'already_cached'}
        reason = result.get('reason', 'source_unavailable')
        deferred = (result.get('status') == 'deferred' and reason == 'GoogleAdmissionError'
                    and result.get('failure_phase') in {'map_create', 'download'}
                    and result.get('admission_reason') in {
                        'concurrency', 'rate', 'cooldown', 'local_queue', 'local_capacity',
                        'coordinator_unavailable', 'startup_timeout', 'saturated'}
                    and result.get('http_status') is None and not result.get('transport_error'))
        if (item['params'].get('source') != 'bdc' and result.get('status') == 'deferred'
                and reason == 'GoogleAdmissionError'
                and result.get('admission_reason') == 'project_restricted'):
            deferred = True
        if item['params'].get('source') == 'bdc' and result.get('status') == 'deferred':
            deferred = reason in {'bdc_selection_pending', 'bdc_origin_busy', 'bdc_origin_paused'}
        expiry = result.get('expires_at')
        if successful and (type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= time.time()):
            successful = False
            reason = 'cache_expired'
        change = {'state': 'completed', 'reason': None, 'next_retry': None,
                  'completed_at': datetime.now(timezone.utc), 'cache_result': result.get('status'),
                  'expires_at': expiry if successful else None,
                  'failure_phase': result.get('failure_phase'),
                  'admission_reason': result.get('admission_reason'),
                  'admission_phase': result.get('admission_phase'),
                  'secondary_error': result.get('secondary_error'),
                  'secondary_phase': result.get('secondary_phase'),
                  'http_status': result.get('http_status'),
                  'transport_error': result.get('transport_error'),
                  'diagnostics': {key: value for key, value in (result.get('diagnostics') or {}).items()
                                  if key in {'validation_code', 'byte_length', 'content_sha256',
                                             'image_format', 'width', 'height'}}}
        if not successful:
            failure = failure_state(reason, 1 if deferred else item['attempts'], result.get('retry_after', 0),
                                    validation_code=change['diagnostics'].get('validation_code'))
            change.update(state=failure['state'], reason=reason, completed_at=None,
                          next_retry=(datetime.now(timezone.utc) + timedelta(seconds=failure['delay'])
                                      if failure['state'] == 'retry_wait' else None))
        evidence = result.get('evidence') or {}
        verified_absence = evidence.get('kind') == 'empty_collection' and item['params'].get('source') != 'bdc'
        if item['params'].get('source') == 'bdc':
            verified_absence = (evidence.get('kind') == 'bdc_selection_no_image'
                                and isinstance(evidence.get('selection_id'), str)
                                and len(evidence['selection_id']) == 64
                                and evidence.get('collection') == job['config']['bdc']['collection'])
        tile_absence = False
        if (item['params'].get('source') == 'bdc' and result.get('status') == 'no_data'
                and evidence.get('kind') in {'bdc_tile_no_valid_source_pixels', 'bdc_tile_outside_asset_bounds'}):
            from app.modules.bdc.tile_validity import PROOF_TTL, absence_kind, checked_time, valid_diagnostics
            params = item['params']
            context = dict(collection=job['config']['bdc']['collection'], item=evidence.get('item'),
                           profile=params['visparam'], **{key: params[key] for key in ('x', 'y', 'z')})
            valid = valid_diagnostics(evidence, context, job['config']['renderer_revision'],
                                      change['diagnostics'].get('content_sha256'))
            if (valid and absence_kind(evidence) == evidence['kind']
                    and change['diagnostics'].get('validation_code') == 'transparent_image'
                    and type(expiry) in (int, float) and math.isfinite(expiry)
                    and time.time() < expiry <= checked_time(evidence).timestamp() + PROOF_TTL):
                tile_absence = bool(self.db.campaignImageSelections.find_one({
                    '_id': evidence.get('selection_id'), 'campaignId': job['_id'],
                    'pointId': item['point_ref'], 'revision': job['config']['bdc']['selectionRevision'],
                    'year': params['year'], 'period': params['period'], 'state': 'ready',
                    'params.collection': context['collection'], 'profiles': context['profile'],
                    'result.complete': True, 'result.best_local_id': context['item'],
                }, {'_id': 1}))
            verified_absence = tile_absence
        if (result.get('status') == 'no_data' and verified_absence
                and type(expiry) in (int, float) and math.isfinite(expiry) and expiry > time.time()
                and type(evidence.get('count')) is int and evidence['count'] == 0
                and evidence.get('collection') and evidence.get('checked_at')
                and evidence.get('period') == item['params']['period']
                and evidence.get('year') == item['params']['year']):
            change.update(state='no_data', reason=('bdc_tile_no_data' if tile_absence else 'bdc_no_image') if item['params'].get('source') == 'bdc' else 'empty_collection', next_retry=None,
                          evidence=evidence, expires_at=expiry, completed_at=datetime.now(timezone.utc))
        update = {'$set': change, '$unset': {'worker_token': ''}}
        if deferred:
            update['$inc'] = {'attempts': -1, 'admission_deferrals': 1}
        return bool(self.items.update_one({'_id': item['_id'], 'run_id': job['run_id'],
                                          'state': 'processing', 'worker_token': token},
                                         update).modified_count)

    def point_summary(self, job, now):
        result = dict(complete=0, partial=0, pending=0, no_data=0)
        if job.get('configuration_changed') or job['status'] == 'stale':
            result['pending'] = self.db.points.count_documents({'campaign': job['_id']})
            return result
        valid = {'$and': [{'$in': ['$state', ['completed', 'no_data']]},
                          {'$gt': [{'$ifNull': ['$expires_at', 0]}, now]},
                          {'$lt': [{'$ifNull': ['$expires_at', 0]}, float('inf')]}]}
        pipeline = [{'$match': {'run_id': job['run_id']}}, {'$group': {
            '_id': '$point_id', 'total': {'$sum': 1},
            'valid': {'$sum': {'$cond': [valid, 1, 0]}},
            'images': {'$sum': {'$cond': [{'$and': [valid, {'$eq': ['$state', 'completed']}]}, 1, 0]}}
        }}]
        complete = {'$and': [bool(job.get('manifest_complete')), {'$eq': ['$valid', '$total']}]}
        category = {'$cond': [complete,
                    {'$cond': [{'$gt': ['$images', 0]}, 'complete', 'no_data']},
                    {'$cond': [{'$gt': ['$valid', 0]}, 'partial', 'pending']}]}
        pipeline.append({'$group': {'_id': category, 'count': {'$sum': 1}}})
        seen = 0
        for row in self.items.aggregate(pipeline, allowDiskUse=True, maxTimeMS=10000):
            seen += row['count']
            result[row['_id']] = row['count']
        result['pending'] += max(0, job['point_count'] - seen)
        return result

    def progress_summary(self, job, cached):
        now = time.time()
        previous = job.get('progress_summary', {})
        if cached and previous.get('status') == job['status'] and 'points' in previous and previous.get('valid_until', 0) > now:
            return previous
        counts = dict.fromkeys(STATES, 0)
        expired_condition = {'$and': [
            {'$in': ['$state', ['completed', 'no_data']]},
            {'$not': {'$and': [
                {'$gt': [{'$ifNull': ['$expires_at', 0]}, now]},
                {'$lt': [{'$ifNull': ['$expires_at', 0]}, float('inf')]},
            ]}},
        ]}
        expired = 0
        valid_until = now + 30
        for row in self.items.aggregate([{'$match': {'run_id': job['run_id']}},
                                        {'$group': {
                                            '_id': {'$cond': [expired_condition, 'blocked', '$state']},
                                            'count': {'$sum': 1},
                                            'expired': {'$sum': {'$cond': [expired_condition, 1, 0]}},
                                            'earliest_expiry': {'$min': {'$cond': [
                                                {'$and': [{'$in': ['$state', ['completed', 'no_data']]},
                                                          {'$gt': ['$expires_at', now]}]},
                                                '$expires_at', float('inf')]}},
                                        }}]):
            counts[row['_id']] = row['count']
            expired += row['expired']
            valid_until = min(valid_until, row['earliest_expiry'])
        summary = {'counts': counts, 'expired': expired, 'status': job['status'],
                   'as_of': now, 'valid_until': valid_until, 'points': self.point_summary(job, now)}
        if cached:
            self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'status': job['status']},
                                 {'$set': {'progress_summary': summary}})
        return summary

    def status(self, campaign_id, page=0, limit=50, cached=False):
        job = self.jobs.find_one({'_id': campaign_id}, {'lease_token': 0})
        if not job:
            return {'status': 'not_started', 'counts': dict.fromkeys(STATES, 0), 'total': 0,
                    'errors': [], 'percent': 0}
        configuration_changed = self.configuration_changed(job)
        if configuration_changed:
            job['status'] = 'stale'
        job['configuration_changed'] = configuration_changed
        summary = self.progress_summary(job, cached)
        counts = summary['counts']
        expired = summary['expired']
        now = time.time()
        total = sum(counts.values())
        expired_query = self.expired_query(job, now)
        if expired and job['status'] == 'completed':
            job['status'] = 'blocked'
        errors_query = {'run_id': job['run_id'], '$or': [
            {'state': {'$in': ['blocked', 'retry_wait', 'no_data']}}, expired_query]}
        errors = list(self.items.find(errors_query, {'worker_token': 0, 'point_ref': 0}).sort('_id', 1).skip(page * limit).limit(limit))
        for item in errors:
            if item['state'] in ('completed', 'no_data') and not (type(item.get('expires_at')) in (float, int) and now < item['expires_at'] < float('inf')):
                item.update(state='blocked', reason='cache_expired')
        job.pop('progress_summary', None)
        return dict(job, counts=counts, total=total, errors=errors,
                    summary_as_of=summary['as_of'], point_counts=summary['points'],
                    error_total=sum(counts[state] for state in ('blocked', 'retry_wait', 'no_data')), page=page,
                    percent=round(100 * (counts['completed'] + counts['no_data']) / total, 2) if total else 0)

    def finish(self, job, token):
        self.requeue_expired(job, token)
        current = self.jobs.find_one({'_id': job['_id'], 'run_id': job['run_id']})
        summary = {'status': current['status'] if current else 'stale', 'total': (current or {}).get('total', 0)}
        if not self.owned(job, token) or summary['status'] in {'preparing', 'stale'}:
            return summary
        active = self.items.find_one({'run_id': job['run_id'], 'state': {'$in': ['pending', 'processing', 'retry_wait']}}, {'_id': 1})
        blocked = self.items.find_one({'run_id': job['run_id'], 'state': 'blocked'}, {'_id': 1})
        status = 'running' if active else ('blocked' if blocked or not summary['total'] else 'completed')
        if status == 'completed' and self.configuration_changed(job):
            status = 'stale'
        self.jobs.update_one({'_id': job['_id'], 'run_id': job['run_id'], 'lease_token': token},
                             {'$set': {'status': status, 'updated_at': datetime.now(timezone.utc)}})
        summary['status'] = status
        return summary
