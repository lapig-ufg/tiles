import os
from pathlib import Path

import requests
from billiard.exceptions import SoftTimeLimitExceeded

from app.modules.bdc.local_quality import CandidateLimitExceeded, IncompleteCatalogError
from app.modules.bdc.selection_jobs import LeaseLost, OriginBusy, public_job, selection_store
from app.modules.bdc.service import BdcService
from app.modules.bdc.transport import ServicePaused, digest
from app.tasks.celery_app import celery_app


def process_selection(store, job_id, service):
    token = store.claim(job_id)
    if token is None:
        job = store.get(job_id)
        return public_job(job) if job else None
    job = store.get(job_id)
    def check():
        store.check_owned(job_id, token)
        store.check_origin()
    try:
        check()
        retry_options = {'retry_token': digest([job_id, job['retry_epoch']])} if job.get('retry_epoch', 0) > 0 else {}
        result = service.select_images(job['params'], check, **retry_options)
        check()
        store.finish(job_id, token, result=result)
    except OriginBusy:
        store.defer(job_id, token)
    except LeaseLost:
        pass
    except Exception as exc:
        if isinstance(exc, ServicePaused):
            store.pause_origin()
            code, retryable = 'origin_paused', True
        elif isinstance(exc, CandidateLimitExceeded):
            code, retryable = 'candidate_limit', False
        elif isinstance(exc, IncompleteCatalogError):
            code, retryable = 'incomplete_catalog', True
        elif isinstance(exc, (TimeoutError, SoftTimeLimitExceeded)):
            code, retryable = 'deadline_exceeded', True
        elif isinstance(exc, requests.exceptions.RequestException):
            code, retryable = 'source_unavailable', True
        elif isinstance(exc, ValueError):
            code, retryable = 'invalid_parameters', False
        else:
            code, retryable = 'read_failed', True
        store.finish(job_id, token, error={'code': code, 'retryable': retryable})
    return public_job(store.get(job_id))


@celery_app.task(queue='bdc', acks_late=True, reject_on_worker_lost=True,
                 soft_time_limit=540, time_limit=600)
def run_bdc_selection(job_id):
    with selection_store() as store:
        result = process_selection(store, job_id, BdcService(Path(os.environ.get('BDC_CACHE_DIR', 'cache/bdc'))))
    return {'id': job_id, 'state': result['state'] if result else 'missing'}


@celery_app.task(queue='maintenance')
def recover_bdc_selections():
    with selection_store() as store:
        for job in store.recoverable():
            if store.reserve_dispatch(job['_id']):
                try:
                    run_bdc_selection.delay(job['_id'])
                except Exception:
                    store.dispatch_failed(job['_id'])
                    raise


celery_app.conf.beat_schedule['bdc-selection-recovery'] = {
    'task': 'app.tasks.bdc_selection.recover_bdc_selections', 'schedule': 15.0,
}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Operação do acesso BDC')
    parser.add_argument('operation', choices=['resume-origin'])
    parser.parse_args(argv)
    with selection_store() as store:
        store.resume_origin()
    print('Origem BDC liberada para solicitações explícitas.')


if __name__ == '__main__':
    main()
