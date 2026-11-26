"""Referências BDC compartilhadas pelas campanhas e pelo processamento offline."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from .selection_jobs import SelectionJobStore, enqueue_selection, public_job


class SelectionInvalid(ValueError):
    pass


class PointUnavailable(RuntimeError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class InvalidSelectionResult(RuntimeError):
    pass


class SelectionQueueUnavailable(RuntimeError):
    pass


def _integer(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and int(value) == value and abs(value) <= 9007199254740991


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _js_json(value):
    if isinstance(value, dict):
        return '{' + ','.join(_js_json(k) + ':' + _js_json(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(_js_json(v) for v in value) + ']'
    if isinstance(value, float):
        if value == 0:
            return '0'
        text = repr(value).lower()
        if 1e-6 <= abs(value) < 1e21:
            from decimal import Decimal
            decimal = format(Decimal(text), 'f')
            return decimal.rstrip('0').rstrip('.') if '.' in decimal else decimal
        if 'e' in text:
            mantissa, exponent = text.split('e')
            return mantissa.removesuffix('.0') + 'e' + ('+' if int(exponent) >= 0 else '-') + str(abs(int(exponent)))
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def identity(campaign, point, year, period):
    config = campaign.get('bdcConfig') or {}
    lon, lat = point.get('lon'), point.get('lat')
    radius, revision = config.get('bufferRadiusM'), config.get('selectionRevision')
    try:
        valid_year = _integer(year) and float(campaign['initialYear']) <= year <= float(campaign['finalYear'])
    except (KeyError, TypeError, ValueError):
        valid_year = False
    if (campaign.get('imagerySource') != 'bdc' or point.get('campaign') != campaign.get('_id')
            or not valid_year or period not in ('DRY', 'WET')
            or not _number(lon) or abs(lon) > 180 or not _number(lat) or abs(lat) > 80
            or config.get('collection') not in ('landsat-2', 'S2_L2A-1')
            or not _integer(revision) or revision < 1 or not _number(radius) or not 100 <= radius <= 5000
            or config.get('selectionStrategy') not in ('local_cloud', 'local_unusable')):
        raise SelectionInvalid('Ponto ou período BDC inválido.')
    year = int(year)
    params = {'collection': config['collection'], 'lon': lon, 'lat': lat, 'radius_m': radius,
              'criterion': 'unusable' if config['selectionStrategy'] == 'local_unusable' else 'cloud',
              'start': str(year) + ('-01-01' if period == 'WET' else '-06-01'),
              'end': str(year) + ('-04-30' if period == 'WET' else '-10-30')}
    key = _js_json([campaign['_id'], point['_id'], revision, year, period, params])
    return {'_id': hashlib.sha256(key.encode('utf-8')).hexdigest(), 'kind': 'selection',
            'campaignId': campaign['_id'], 'pointId': point['_id'], 'revision': revision,
            'year': year, 'period': period, 'params': params, 'profiles': list(config.get('profiles') or []),
            'state': 'pending', 'updateVersion': 0, 'createdAt': datetime.now(timezone.utc)}


def valid_remote(remote, params, job_id=None):
    if (not isinstance(remote, dict) or not isinstance(remote.get('id'), str)
            or not re.fullmatch('[a-f0-9]{64}', remote['id']) or job_id and remote['id'] != job_id
            or remote.get('state') not in ('pending', 'running', 'ready', 'no_image', 'failed')):
        return False
    if remote.get('params') is not None and remote['params'] != params:
        return False
    if remote['state'] not in ('ready', 'no_image'):
        return True
    result = remote.get('result')
    if not isinstance(result, dict) or result.get('complete') is not True:
        return False
    rows = result.get('rows')
    if (not isinstance(rows, list) or len(rows) > 22020 or not _integer(result.get('evaluated'))
            or result['evaluated'] != len(rows) or not _integer(result.get('eligible'))):
        return False
    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
           or not re.fullmatch('[A-Za-z0-9_.-]{1,180}', row['id']) or row['id'] in ('.', '..') for row in rows):
        return False
    eligible = {row['id'] for row in rows if row.get('eligible') is True}
    if len({row['id'] for row in rows}) != len(rows) or result['eligible'] != len(eligible):
        return False
    if remote['state'] == 'no_image':
        return not eligible and not result.get('best_local_id') and not result.get('best_global_id')
    local_id, global_id = result.get('best_local_id'), result.get('best_global_id')
    return (isinstance(local_id, str) and local_id in eligible
            and (not global_id or isinstance(global_id, str) and global_id in eligible))


def resolve_selection(db, campaign, point, year, period, retry=False, retry_request=None):
    selections = db['campaignImageSelections']

    def available():
        current = db['points'].find_one({'_id': point['_id'], 'campaign': point['campaign']})
        if current is None:
            selections.delete_many({'campaignId': point['campaign'], 'pointId': point['_id']})
            raise PointUnavailable('Ponto não encontrado.', 404)
        if current.get('lon') != point.get('lon') or current.get('lat') != point.get('lat'):
            raise PointUnavailable('As coordenadas do ponto foram alteradas.', 409)
        if current.get('pointMutationLock'):
            raise PointUnavailable('O ponto está em manutenção.', 409)

    available()
    seed = identity(campaign, point, year, period)
    saved = selections.find_one({'_id': seed['_id']})
    if saved is None:
        available()
        try:
            selections.update_one({'_id': seed['_id']}, {'$setOnInsert': seed}, upsert=True)
        except DuplicateKeyError:
            pass
        saved = selections.find_one({'_id': seed['_id']})
    available()
    if saved is None:
        raise PointUnavailable('Referência removida durante manutenção.', 409)
    if saved['state'] in ('ready', 'no_image'):
        if not valid_remote({**saved, 'id': saved.get('jobId', saved['_id'])}, seed['params']):
            raise InvalidSelectionResult('Referência BDC histórica inválida.')
        if any(profile not in saved.get('profiles', []) for profile in seed['profiles']):
            available()
            selections.update_one({'_id': seed['_id']}, {'$addToSet': {'profiles': {'$each': seed['profiles']}}})
            available()
            saved = selections.find_one({'_id': seed['_id']})
            if saved is None:
                raise PointUnavailable('Referência removida durante manutenção.', 409)
        return saved
    retrying = retry is True and saved['state'] == 'failed' and (saved.get('error') or {}).get('retryable') is True
    if saved['state'] == 'failed' and not retrying:
        return saved
    store = SelectionJobStore(db)
    job = store.submit(seed['params'], seed['_id'], retry=retrying, retry_request=retry_request) if retrying or not saved.get('jobId') else store.get(saved['jobId'])
    if job is None or not valid_remote(public_job(job), seed['params'], saved.get('jobId')):
        raise InvalidSelectionResult('Resultado BDC incompleto.')
    available()
    if store.reserve_dispatch(job['_id']):
        try:
            enqueue_selection(job['_id'])
        except Exception as exc:
            store.dispatch_failed(job['_id'])
            raise SelectionQueueUnavailable('Fila de seleção BDC indisponível.') from exc
    states = ['failed', 'pending', 'running'] if retrying else ['pending'] if job['state'] == 'pending' else ['pending', 'running']
    version = saved['updateVersion'] if _integer(saved.get('updateVersion')) else {'$exists': False}
    available()
    selections.update_one({'_id': seed['_id'], 'state': {'$in': states}, 'updateVersion': version},
                          {'$set': {'jobId': job['_id'], 'state': job['state'], 'result': job.get('result'),
                                    'error': job.get('error'), 'algorithmVersion': job.get('algorithm_version'),
                                    'updatedAt': datetime.now(timezone.utc)}, '$inc': {'updateVersion': 1}})
    available()
    saved = selections.find_one({'_id': seed['_id']})
    if saved is None:
        raise PointUnavailable('Referência removida durante manutenção.', 409)
    if saved['state'] in ('ready', 'no_image') and not valid_remote({**saved, 'id': saved.get('jobId', saved['_id'])}, seed['params']):
        raise InvalidSelectionResult('Referência BDC histórica inválida.')
    return saved
