from typing import Literal
from datetime import datetime, timezone
from fastapi.encoders import jsonable_encoder

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field
from bson import ObjectId

from app.core.auth import SuperAdminRequired
from app.tasks.offline_campaign import campaign_store, run_offline_campaign

def encode_status(value):
    return jsonable_encoder(value, custom_encoder={datetime: lambda date: date.replace(tzinfo=timezone.utc).isoformat(), ObjectId: str})


router = APIRouter(prefix='/api/cache/campaign', dependencies=[SuperAdminRequired])


class OfflineCacheRequest(BaseModel):
    mode: Literal['start', 'retry_failed', 'regenerate'] = 'start'
    width: int = Field(default=512, ge=256, le=2048)
    height: int = Field(default=512, ge=256, le=2048)


@router.get('/{campaign_id}/offline')
def get_offline_status(campaign_id: str = Path(min_length=1, max_length=200),
                       page: int = Query(default=0, ge=0, le=100000),
                       limit: int = Query(default=50, ge=1, le=100)):
    with campaign_store() as store:
        if not store.db.campaign.find_one({'_id': campaign_id}, {'_id': 1}):
            raise HTTPException(404, 'Campanha não encontrada')
        return encode_status(store.status(campaign_id, page, limit, cached=True))


@router.post('/{campaign_id}/offline')
def start_offline_cache(request: OfflineCacheRequest,
                        campaign_id: str = Path(min_length=1, max_length=200)):
    with campaign_store() as store:
        try:
            store.start(campaign_id, request.mode, request.width, request.height)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # O estado persiste antes da publicação; o reconciliador recupera falhas do broker.
        try:
            run_offline_campaign.delay(campaign_id)
        except Exception:
            return encode_status(dict(store.status(campaign_id), dispatch_pending=True))
        return encode_status(store.status(campaign_id))
