"""Validação pura `(year, visparam, composite_mode)` antes de bater no EE.

Evita chamadas previsivelmente inválidas (ano fora do range das coleções,
visparam desconhecido). Casos determinísticos são bons candidatos para
cache negativo com TTL longo.
"""
from __future__ import annotations

import pytest

from app.visualization.validation import (
    validate_landsat_request,
    ValidationError,
    REASON_INVALID_YEAR,
    REASON_INVALID_VISPARAM,
    REASON_INVALID_COMPOSITE,
)


KNOWN_VISPARAMS = {"landsat-tvi-true", "landsat-tvi-false", "landsat-tvi-agri"}


def test_known_visparams_derived_from_visParam_hardcoded():
    """KNOWN_LANDSAT_VISPARAMS deve refletir VISPARAMS em visParam.py.

    Se um novo `landsat-*` for adicionado lá, deve aparecer aqui sem
    atualização manual. Evita incidente silencioso (422 em request legítimo).
    """
    from app.visualization.visParam import VISPARAMS as HARDCODED
    from app.visualization.validation import KNOWN_LANDSAT_VISPARAMS

    expected = {k for k in HARDCODED if k.startswith("landsat-")}
    assert KNOWN_LANDSAT_VISPARAMS == expected


class TestValidYears:
    def test_accepts_year_in_L5_range(self):
        assert validate_landsat_request(2000, "landsat-tvi-true", "BEST_IMAGE") is None

    def test_accepts_year_in_L7_range(self):
        assert validate_landsat_request(2012, "landsat-tvi-true", "MOSAIC") is None

    def test_accepts_year_in_L8_range(self):
        assert validate_landsat_request(2020, "landsat-tvi-agri", "BEST_IMAGE") is None


class TestInvalidYear:
    def test_rejects_year_before_L5_launch(self):
        err = validate_landsat_request(1983, "landsat-tvi-true", "BEST_IMAGE")
        assert isinstance(err, ValidationError)
        assert err.reason == REASON_INVALID_YEAR
        assert err.deterministic is True

    def test_rejects_year_too_far_in_future(self):
        err = validate_landsat_request(2100, "landsat-tvi-true", "BEST_IMAGE")
        assert err is not None
        assert err.reason == REASON_INVALID_YEAR
        assert err.deterministic is True


class TestInvalidVisparam:
    def test_rejects_unknown_visparam(self):
        err = validate_landsat_request(2020, "nao-existe", "BEST_IMAGE")
        assert err is not None
        assert err.reason == REASON_INVALID_VISPARAM
        assert err.deterministic is True

    @pytest.mark.parametrize("vp", list(KNOWN_VISPARAMS))
    def test_accepts_known_visparams(self, vp):
        assert validate_landsat_request(2020, vp, "BEST_IMAGE") is None


class TestInvalidCompositeMode:
    def test_rejects_unknown_composite_mode(self):
        err = validate_landsat_request(2020, "landsat-tvi-true", "WEIRD")
        assert err is not None
        assert err.reason == REASON_INVALID_COMPOSITE

    def test_accepts_BEST_IMAGE(self):
        assert validate_landsat_request(2020, "landsat-tvi-true", "BEST_IMAGE") is None

    def test_accepts_MOSAIC(self):
        assert validate_landsat_request(2020, "landsat-tvi-true", "MOSAIC") is None


class TestValidationErrorShape:
    def test_error_has_status_422(self):
        err = validate_landsat_request(1983, "landsat-tvi-true", "BEST_IMAGE")
        assert err.status_code == 422

    def test_error_ttl_is_long_for_deterministic(self):
        """Ano fora do range é determinístico — TTL longo (>=1 dia)."""
        err = validate_landsat_request(1983, "landsat-tvi-true", "BEST_IMAGE")
        assert err.ttl_seconds >= 86400
