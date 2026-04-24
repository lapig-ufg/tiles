"""`_error_png_bytes` deve cachear por `reason` — evita render Pillow repetido.

Usa `cache_info()` de lru_cache diretamente para não depender de monkeypatch
em `generate_error_image`, que seria frágil sob pollution de sys.modules.
"""
from __future__ import annotations

from app.core import errors


def test_same_reason_renders_only_once():
    errors._error_png_bytes.cache_clear()

    errors._error_png_bytes("ee_rate_limit")
    errors._error_png_bytes("ee_rate_limit")
    errors._error_png_bytes("ee_rate_limit")

    info = errors._error_png_bytes.cache_info()
    assert info.misses == 1, f"esperado 1 render, teve {info.misses}"
    assert info.hits == 2


def test_different_reasons_render_independently():
    errors._error_png_bytes.cache_clear()

    errors._error_png_bytes("ee_rate_limit")
    errors._error_png_bytes("ee_band_missing")
    errors._error_png_bytes("ee_rate_limit")

    info = errors._error_png_bytes.cache_info()
    assert info.misses == 2, "dois reasons distintos = dois renders"
    assert info.hits == 1


def test_cache_size_capped():
    """maxsize=8 previne explosão de memória se reasons proliferarem."""
    assert errors._error_png_bytes.cache_info().maxsize == 8
