"""Allowlist do cog-proxy configurável por ambiente (spec tvi-api 2026-08-12 §7)."""
import pytest
from fastapi import HTTPException

from app.api import cog_proxy


def test_host_padrao_continua_permitido(monkeypatch):
    monkeypatch.setattr(cog_proxy.settings, "COG_ALLOWED_HOSTS", ["data.inpe.br"], raising=False)
    assert cog_proxy._validate_url("https://data.inpe.br/bdc/x.tif") == "https://data.inpe.br/bdc/x.tif"


def test_host_extra_liberado_por_configuracao(monkeypatch):
    monkeypatch.setattr(
        cog_proxy.settings, "COG_ALLOWED_HOSTS", ["data.inpe.br", "meu-acervo.org"], raising=False
    )
    assert cog_proxy._validate_url("https://meu-acervo.org/a.tif").startswith("https://meu-acervo.org")


def test_host_fora_da_lista_recebe_403(monkeypatch):
    monkeypatch.setattr(cog_proxy.settings, "COG_ALLOWED_HOSTS", ["data.inpe.br"], raising=False)
    with pytest.raises(HTTPException) as exc:
        cog_proxy._validate_url("https://terceiro.example/a.tif")
    assert exc.value.status_code == 403


def test_lista_em_string_separada_por_virgulas(monkeypatch):
    monkeypatch.setattr(
        cog_proxy.settings, "COG_ALLOWED_HOSTS", "data.inpe.br, meu-acervo.org", raising=False
    )
    assert cog_proxy._validate_url("https://meu-acervo.org/a.tif").startswith("https://meu-acervo.org")


def test_protecoes_existentes_seguem_valendo(monkeypatch):
    monkeypatch.setattr(cog_proxy.settings, "COG_ALLOWED_HOSTS", ["data.inpe.br"], raising=False)
    with pytest.raises(HTTPException) as scheme:
        cog_proxy._validate_url("ftp://data.inpe.br/a.tif")
    assert scheme.value.status_code == 400
    with pytest.raises(HTTPException) as userinfo:
        cog_proxy._validate_url("https://user:pass@data.inpe.br/a.tif")
    assert userinfo.value.status_code == 400
