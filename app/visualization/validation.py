"""Validação pura de requisições Landsat antes de chamar o Earth Engine.

Funções aqui são síncronas e sem I/O: ideais para ser chamadas no topo do
handler e para servir como chave determinística em cache negativo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


REASON_INVALID_YEAR = "invalid_year"
REASON_INVALID_VISPARAM = "invalid_visparam"
REASON_INVALID_COMPOSITE = "invalid_composite_mode"

# Landsat 5 foi lançado em 1984. Tiles anteriores não existem na coleção L5 C02.
MIN_LANDSAT_YEAR = 1984

# Modos de composição suportados pelo endpoint Landsat.
VALID_COMPOSITE_MODES = frozenset({"BEST_IMAGE", "MOSAIC"})

# Conjunto de visparams Landsat suportados — derivado dinamicamente de
# `visParam.VISPARAMS` para evitar divergência silenciosa: se o time
# adicionar um `landsat-*` novo, aparece aqui automaticamente.
def _known_landsat_visparams() -> frozenset[str]:
    from app.visualization.visParam import VISPARAMS as _V
    return frozenset(k for k in _V if k.startswith("landsat-"))


# Materializado uma vez no import; funciona porque visParam é hardcoded (dict
# literal no módulo). Se no futuro a fonte virar dinâmica (MongoDB runtime),
# converter para chamada no corpo de `validate_landsat_request`.
KNOWN_LANDSAT_VISPARAMS = _known_landsat_visparams()


@dataclass(frozen=True)
class ValidationError:
    reason: str                 # código machine-readable (X-Error-Reason)
    status_code: int = 422      # semântica "Unprocessable Entity"
    deterministic: bool = True  # se True, candidato a cache negativo longo
    ttl_seconds: int = 86400    # 1 dia para determinístico


def _max_landsat_year() -> int:
    """Ano máximo aceitável = ano corrente + 1 (margem para início de Janeiro)."""
    return datetime.now(timezone.utc).year + 1


def validate_landsat_request(
    year: int, visparam: str, composite_mode: str
) -> ValidationError | None:
    """Retorna `None` se a combinação é válida; `ValidationError` caso contrário."""
    if year < MIN_LANDSAT_YEAR or year > _max_landsat_year():
        return ValidationError(reason=REASON_INVALID_YEAR)

    if visparam not in KNOWN_LANDSAT_VISPARAMS:
        return ValidationError(reason=REASON_INVALID_VISPARAM)

    if composite_mode not in VALID_COMPOSITE_MODES:
        return ValidationError(reason=REASON_INVALID_COMPOSITE)

    return None
