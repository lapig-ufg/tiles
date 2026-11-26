import re
import time
import uuid
from threading import Event, Thread
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .transport import ServicePaused, digest

ALGORITHM_VERSION = 'bdc-local-quality-v1'
LEASE_SECONDS = 660
ORIGIN_SLOTS = 2
TERMINAL = {'ready', 'no_image', 'failed'}


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    collection: Literal['landsat-2', 'S2_L2A-1']
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-80, le=80)
    radius_m: float = Field(default=1000, ge=100, le=5000)
    start: date
    end: date
    criterion: Literal['cloud', 'unusable'] = 'cloud'
    idempotency_key: str = Field(min_length=1, max_length=180, pattern=r'^[A-Za-z0-9_.:-]+$')
    retry: bool = False

    @model_validator(mode='after')
    def validate_period(self):
        if self.start > self.end or (self.end - self.start).days > 366:
            raise ValueError('Escolha um intervalo de até 366 dias')
        return self

    def parameters(self):
        return self.model_dump(mode='json', exclude={'idempotency_key', 'retry'})


class IdempotencyConflict(ValueError):
    pass


class OriginBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


@contextmanager
def selection_store():
    from app.core.config import settings
    with MongoClient(settings.get('MONGODB_URL', 'mongodb://localhost:27017'),
                     serverSelectionTimeoutMS=5000, socketTimeoutMS=15000) as client:
        yield SelectionJobStore(client[settings.get('MONGODB_DB', 'tvi')])


class SelectionJobStore:
    def __init__(self, database):
        self.db = database
        self.jobs = database.bdc_selection_jobs
        self.keys = database.bdc_selection_keys
        self.origin = database.bdc_origin_leases
        self.jobs.create_index([('state', 1), ('lease_until', 1), ('updated_at', 1)])

    def submit(self, params, key, retry=False, retry_request=None):
        params = SelectionRequest(**params, idempotency_key=key).parameters()
        job_id = digest({'params': params, 'algorithm_version': ALGORITHM_VERSION})
        key_id = digest(key)
        try:
            self.keys.insert_one({'_id': key_id, 'job_id': job_id})
        except DuplicateKeyError:
            if self.keys.find_one({'_id': key_id})['job_id'] != job_id:
                raise IdempotencyConflict('Chave idempotente associada a outros parâmetros')
        now = datetime.now(timezone.utc)
        try:
            self.jobs.insert_one({'_id': job_id, 'params_hash': job_id, 'params': params,
                                  'algorithm_version': ALGORITHM_VERSION, 'state': 'pending',
                                  'created_at': now, 'updated_at': now, 'lease_until': now,
                                  'result': None, 'error': None, 'retry_epoch': 0})
        except DuplicateKeyError:
            pass
        if retry:
            query = {'_id': job_id, 'state': 'failed', 'error.retryable': True}
            update = {'$set': {'state': 'pending', 'lease_until': now, 'updated_at': now,
                               'error': None, 'result': None}, '$inc': {'retry_epoch': 1}}
            if retry_request is not None:
                request_id = digest(retry_request)
                query['offline_retry_requests'] = {'$ne': request_id}
                update['$addToSet'] = {'offline_retry_requests': request_id}
            self.jobs.update_one(query, update)
        return self.get(job_id)

    def get(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{64}', job_id):
            return None
        return self.jobs.find_one({'_id': job_id})

    def claim(self, job_id):
        now = datetime.now(timezone.utc)
        token = uuid.uuid4().hex
        job = self.jobs.find_one_and_update(
            {'_id': job_id, 'state': {'$in': ['pending', 'running']}, 'lease_until': {'$lte': now}},
            {'$set': {'state': 'running', 'lease_token': token, 'updated_at': now,
                      'lease_until': now + timedelta(seconds=LEASE_SECONDS)}, '$inc': {'attempts': 1}},
            return_document=ReturnDocument.AFTER)
        return token if job else None

    def check_owned(self, job_id, token):
        if not self.jobs.find_one({'_id': job_id, 'state': 'running', 'lease_token': token,
                                   'lease_until': {'$gt': datetime.now(timezone.utc)}}, {'_id': 1}):
            raise LeaseLost('Posse do trabalho expirada')

    def finish(self, job_id, token, *, result=None, error=None):
        if result is not None:
            rows = result.get('rows', [])
            eligible = {row['id'] for row in rows if row.get('eligible')}
            if (result.get('complete') is not True or result.get('evaluated') != len(rows)
                    or result.get('eligible') != len(eligible)
                    or len({row['id'] for row in rows}) != len(rows)
                    or (result.get('best_local_id') not in eligible if eligible else result.get('best_local_id') is not None)
                    or result.get('best_global_id') is not None and result['best_global_id'] not in eligible):
                raise ValueError('Seleção parcial recusada')
        if (result is None) == (error is None):
            raise ValueError('Resultado terminal inválido')
        now = datetime.now(timezone.utc)
        state = 'failed' if error else ('ready' if result['best_local_id'] else 'no_image')
        return bool(self.jobs.update_one(
            {'_id': job_id, 'state': 'running', 'lease_token': token, 'lease_until': {'$gt': now}},
            {'$set': {'state': state, 'result': result, 'error': error, 'updated_at': now,
                      'completed_at': now}, '$unset': {'lease_token': ''}}).modified_count)

    def defer(self, job_id, token):
        now = datetime.now(timezone.utc)
        return bool(self.jobs.update_one({'_id': job_id, 'state': 'running', 'lease_token': token},
                    {'$set': {'state': 'pending', 'lease_until': now + timedelta(seconds=5), 'updated_at': now},
                     '$unset': {'lease_token': ''}, '$inc': {'attempts': -1}}).modified_count)

    def recoverable(self):
        return self.jobs.find({'state': {'$in': ['pending', 'running']},
                               'lease_until': {'$lte': datetime.now(timezone.utc)}}, {'_id': 1}).sort('updated_at', 1).limit(20)

    def reserve_dispatch(self, job_id):
        now = datetime.now(timezone.utc)
        return bool(self.jobs.find_one_and_update(
            {'_id': job_id, 'state': {'$in': ['pending', 'running']}, 'lease_until': {'$lte': now},
             '$or': [{'dispatch_until': {'$exists': False}}, {'dispatch_until': {'$lte': now}}]},
            {'$set': {'dispatch_until': now + timedelta(seconds=30)}}))

    def dispatch_failed(self, job_id):
        self.jobs.update_one({'_id': job_id}, {'$unset': {'dispatch_until': ''}})

    def pause_origin(self):
        self.origin.update_one({'_id': 'paused'}, {'$set': {'paused': True}}, upsert=True)

    def resume_origin(self):
        self.origin.update_one({'_id': 'paused'}, {'$set': {'paused': False, 'resumed_at': time.time()}}, upsert=True)

    def origin_resumed_since(self, timestamp):
        return bool(self.origin.find_one({'_id': 'paused', 'paused': False, 'resumed_at': {'$gt': timestamp}}))

    def check_origin(self):
        if self.origin.find_one({'_id': 'paused', 'paused': True}):
            raise ServicePaused('Origem BDC pausada')

    @contextmanager
    def origin_admission(self):
        self.check_origin()
        now = datetime.now(timezone.utc)
        token = uuid.uuid4().hex
        claimed = None
        for slot in range(ORIGIN_SLOTS):
            slot_id = f'origin:{slot}'
            try:
                self.origin.insert_one({'_id': slot_id, 'lease_until': now})
            except DuplicateKeyError:
                pass
            claimed = self.origin.find_one_and_update(
                {'_id': slot_id, 'lease_until': {'$lte': now}},
                {'$set': {'token': token, 'lease_until': now + timedelta(seconds=LEASE_SECONDS)}},
                return_document=ReturnDocument.AFTER)
            if claimed:
                break
        if not claimed:
            raise OriginBusy('Orçamento agregado de leituras BDC ocupado')
        stop = Event()
        lost = Event()
        def check():
            if lost.is_set():
                raise LeaseLost('Posse do orçamento BDC expirada')
            self.check_origin()
        def renew():
            while not stop.wait(min(30, LEASE_SECONDS / 3)):
                try:
                    now = datetime.now(timezone.utc)
                    updated = self.origin.update_one(
                        {'_id': claimed['_id'], 'token': token, 'lease_until': {'$gt': now}},
                        {'$set': {'lease_until': now + timedelta(seconds=LEASE_SECONDS)}})
                    if updated.matched_count:
                        continue
                except Exception:
                    pass
                lost.set()
                return
        heartbeat = Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            check()
            yield check
        except ServicePaused:
            self.pause_origin()
            raise
        finally:
            stop.set()
            heartbeat.join()
            self.origin.update_one({'_id': claimed['_id'], 'token': token},
                                   {'$set': {'lease_until': datetime.now(timezone.utc)}, '$unset': {'token': ''}})


def public_job(job):
    return {**{key: job.get(key) for key in ('state', 'params', 'algorithm_version', 'params_hash',
                                           'created_at', 'updated_at', 'result', 'error')},
            'id': job['_id'], 'poll_after_seconds': 3 if job['state'] not in TERMINAL else 0}


def enqueue_selection(job_id):
    from app.tasks.bdc_selection import run_bdc_selection
    run_bdc_selection.delay(job_id)


def create_selection_router(store_factory=selection_store, enqueue=enqueue_selection):
    from fastapi import APIRouter, HTTPException, Path, Response
    from fastapi import Depends
    from .auth import verify_bdc_service
    from .http import BdcRoute

    router = APIRouter(dependencies=[Depends(verify_bdc_service)], route_class=BdcRoute)

    @router.post('/selection-jobs')
    def create_selection(body: SelectionRequest, response: Response):
        with store_factory() as store:
            try:
                job = store.submit(body.parameters(), body.idempotency_key, body.retry)
            except IdempotencyConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            if store.reserve_dispatch(job['_id']):
                try:
                    enqueue(job['_id'])
                except Exception as exc:
                    store.dispatch_failed(job['_id'])
                    raise HTTPException(503, {'code': 'queue_unavailable', 'id': job['_id']},
                                        headers={'Retry-After': '3'}) from exc
        response.status_code = 200 if job['state'] in TERMINAL else 202
        response.headers['Cache-Control'] = 'no-store'
        return public_job(job)

    @router.get('/selection-jobs/{job_id}')
    def get_selection(response: Response, job_id: str = Path(pattern=r'^[a-f0-9]{64}$')):
        with store_factory() as store:
            job = store.get(job_id)
        if job is None:
            raise HTTPException(404, 'Trabalho BDC não encontrado')
        response.headers['Cache-Control'] = 'no-store'
        return public_job(job)

    return router
