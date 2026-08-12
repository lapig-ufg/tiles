"""
Tokens GEE efêmeros emitidos pelo tvi-api para operações de campanha
(spec tvi-api 2026-08-12 §8). O refresh token NUNCA chega aqui — apenas
access tokens de curta duração, renovados sob demanda pelo endpoint interno
POST /internal/gee/token (header x-internal-token).
"""
import threading
import time

import requests

from app.core.config import settings, logger

_RENEW_MARGIN_S = 300
_cache: dict = {}
_lock = threading.Lock()


class TviTokenDenied(Exception):
    """tvi-api negou a credencial (conta revogada/campanha desvinculada) — não retentar."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"tvi-api negou token GEE ({status}): {detail}")
        self.status = status
        self.detail = detail


def _post(campaign_id: str):
    return requests.post(
        f"{settings.TVI_API_URL}/api/v1/internal/gee/token",
        json={"campaignId": campaign_id},
        headers={"x-internal-token": settings.TVI_INTERNAL_TOKEN},
        timeout=15,
    )


def get_campaign_token(campaign_id: str) -> dict:
    """Access token GEE da campanha, cacheado até 5 min antes do vencimento.

    Retorna ``{"access_token", "project_id", "expires_at"}`` (epoch em ms).
    403/404/409/422 viram :class:`TviTokenDenied` (erro de negócio, não
    retentável); rede/5xx sobem como exceção normal (retentável).
    """
    with _lock:
        entry = _cache.get(campaign_id)
        if entry and entry["expires_at"] / 1000 - time.time() > _RENEW_MARGIN_S:
            return entry

    resp = _post(campaign_id)
    if resp.status_code in (403, 404, 409, 422):
        raise TviTokenDenied(resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()
    entry = {
        "access_token": data["accessToken"],
        "project_id": data["projectId"],
        "expires_at": data["expiresAt"],
    }
    with _lock:
        _cache[campaign_id] = entry
    logger.info(f"Token GEE renovado para campanha {campaign_id} (modo {data.get('mode')})")
    return entry


def clear_cache() -> None:
    with _lock:
        _cache.clear()
