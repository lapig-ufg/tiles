"""Métricas Prometheus do endpoint de tile (PR #6).

Exposição via endpoint `/metrics` em `main.py`; instrumentação via middleware
ASGI que inspeciona status e header `X-Error-Reason` nas respostas.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram


# -- Registry -----------------------------------------------------------------

tile_requests_total = Counter(
    "tile_requests_total",
    "Contagem de requisições de tile por layer/status/error_reason",
    labelnames=("layer", "status_class", "error_reason"),
)

tile_duration_seconds = Histogram(
    "tile_duration_seconds",
    "Latência de requisições de tile, em segundos",
    labelnames=("layer",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

cache_hits_total = Counter(
    "cache_hits_total",
    "Outcome do cache por layer e tipo (png_hit, png_miss, meta_hit, meta_miss, neg_hit)",
    labelnames=("layer", "type"),
)


# -- Métricas do pool de Service Accounts do GEE ------------------------------

gee_sa_http_429_total = Counter(
    "gee_sa_http_429_total",
    "Contagem de 429 do endpoint de tiles do EE por service account",
    labelnames=("sa_name",),
)

gee_sa_rotation_total = Counter(
    "gee_sa_rotation_total",
    "Rotações de SA realizadas por trigger (http_429 ou rest_api_429)",
    labelnames=("from_sa", "to_sa", "trigger"),
)

gee_sa_in_cooldown = Gauge(
    "gee_sa_in_cooldown",
    "1 se a SA está em cooldown por 429, 0 caso contrário",
    labelnames=("sa_name",),
)

gee_tile_url_regen_total = Counter(
    "gee_tile_url_regen_total",
    "Regenerações de URL do EE disparadas por 429, por layer",
    labelnames=("layer",),
)


# -- Classificação ------------------------------------------------------------

def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


def layer_from_path(path: str) -> str:
    """Classifica path em layer de negócio para uso como label Prometheus.

    Retorna `"other"` para rotas administrativas (/health, /metrics, etc.),
    para que o caller decida se observa ou não.
    """
    if "landsat" in path and "/imagery/" not in path:
        return "landsat"
    if "s2_harmonized" in path:
        return "s2_harmonized"
    if "/imagery/" in path:
        return "imagery"
    return "other"


_OBSERVABLE_LAYERS = {"landsat", "s2_harmonized", "imagery"}


def observe_request(
    path: str,
    status_code: int,
    error_reason: str | None,
    duration_seconds: float,
) -> None:
    """Registra uma requisição finalizada. Ignora rotas fora dos layers de tile."""
    layer = layer_from_path(path)
    if layer not in _OBSERVABLE_LAYERS:
        return

    reason = error_reason if error_reason else "ok"
    tile_requests_total.labels(
        layer=layer,
        status_class=status_class(status_code),
        error_reason=reason,
    ).inc()
    tile_duration_seconds.labels(layer=layer).observe(duration_seconds)


def observe_cache_hit(layer: str, type_: str) -> None:
    """Conta outcomes de cache. `type_` é uma das strings livres:
    png_hit, png_miss, meta_hit, meta_miss, neg_hit."""
    cache_hits_total.labels(layer=layer, type=type_).inc()
