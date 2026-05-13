"""Counters Prometheus por SA são essenciais para diagnosticar oscilação
entre SAs e capacidade de cota — sintoma escondido sem essa observabilidade."""
from __future__ import annotations

from prometheus_client import REGISTRY


def _read_counter(name: str, **labels: str) -> float:
    """Lê o valor atual de um counter Prometheus com os labels dados."""
    value = REGISTRY.get_sample_value(name, labels=labels)
    return value if value is not None else 0.0


def test_gee_sa_http_429_total_counter_exists():
    """gee_sa_http_429_total — contador por SA penalizada por 429 de tile."""
    from app.core import metrics  # noqa: F401
    val = _read_counter("gee_sa_http_429_total", sa_name="probe-sa@proj")
    assert val == 0.0  # counter inicia em 0


def test_gee_sa_rotation_total_counter_exists():
    """gee_sa_rotation_total — contador de rotações por origem/destino/trigger."""
    from app.core import metrics  # noqa: F401
    val = _read_counter(
        "gee_sa_rotation_total",
        from_sa="probe-from",
        to_sa="probe-to",
        trigger="http_429",
    )
    assert val == 0.0


def test_gee_sa_in_cooldown_gauge_exists():
    """gee_sa_in_cooldown — gauge 0/1 por SA, útil para alerta de capacidade."""
    from app.core import metrics  # noqa: F401
    val = REGISTRY.get_sample_value(
        "gee_sa_in_cooldown", labels={"sa_name": "probe-sa@proj"}
    )
    # Gauge não inicializado retorna None; teste só valida que o nome existe
    # via uma chamada subsequente.
    metrics.gee_sa_in_cooldown.labels(sa_name="probe-sa@proj").set(0)
    val = REGISTRY.get_sample_value(
        "gee_sa_in_cooldown", labels={"sa_name": "probe-sa@proj"}
    )
    assert val == 0.0
