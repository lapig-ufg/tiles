import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from .background import BDC_OFFLINE_PARALLELISM
from .campaign_selection import resolve_selection
from .offline_tiles import warm_tile
from .selection_jobs import OriginBusy
from .service import BdcService
from .transport import ServicePaused


def generate_tile(db, campaign, point, params, *, force=False, retry=False, retry_request=None):
    try:
        selected = resolve_selection(db, campaign, point, params['year'], params['period'], retry=retry, retry_request=retry_request)
        if selected['state'] in {'pending', 'running'}:
            return {'status': 'deferred', 'reason': 'bdc_selection_pending', 'retry_after': 30}
        if selected['state'] == 'failed':
            return {'status': 'failed', 'reason': 'bdc_selection_failed'}
        if selected['state'] == 'no_image':
            checked = datetime.now(timezone.utc)
            # A validade atesta a referência histórica, não uma nova consulta ao catálogo.
            expires_at = checked.timestamp() + 30 * 86400
            return {'status': 'no_data', 'expires_at': expires_at,
                    'evidence': {'kind': 'bdc_selection_no_image', 'selection_id': selected['_id'],
                                 'collection': selected['params']['collection'], 'count': 0,
                                 'year': params['year'], 'period': params['period'], 'checked_at': checked,
                                 'reference_completed_at': selected.get('updatedAt') or selected['createdAt']}}
        if selected['state'] != 'ready':
            return {'status': 'failed', 'reason': 'bdc_selection_failed'}
        service = BdcService(Path(os.environ.get('BDC_CACHE_DIR', 'cache/bdc')), background=True)
        result = warm_tile(service, selected['params']['collection'], selected['result']['best_local_id'],
                         params['visparam'], params['z'], params['x'], params['y'], force=force,
                         admission_count=BDC_OFFLINE_PARALLELISM)
        if result.get('status') == 'no_data':
            result['evidence'] = dict(result.get('evidence') or {}, selection_id=selected['_id'],
                                      year=params['year'], period=params['period'])
        return result
    except (OriginBusy, HTTPException) as exc:
        if isinstance(exc, HTTPException) and exc.status_code != 503:
            raise
        return {'status': 'deferred', 'reason': 'bdc_origin_busy', 'retry_after': 30}
    except ServicePaused:
        return {'status': 'deferred', 'reason': 'bdc_origin_paused', 'retry_after': 300}
