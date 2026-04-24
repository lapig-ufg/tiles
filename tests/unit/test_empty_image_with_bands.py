"""Testes do helper `_empty_image_with_bands` — substitui
`ee.Image.constant(0).rename(['empty'])` quando a coleção Landsat filtrada
está vazia, garantindo que o `getMapId` subsequente receba uma imagem que
possui as bandas esperadas (evita `no band named SR_B4`).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api import layers


def test_calls_constant_with_list_of_zeros_matching_band_count(monkeypatch):
    calls = {}

    def fake_constant(value):
        calls["constant_arg"] = value
        return MagicMock(name="image")

    fake_image = MagicMock()
    fake_image.rename.return_value.updateMask.return_value = fake_image

    import ee
    monkeypatch.setattr(ee.Image, "constant", staticmethod(fake_constant))

    layers._empty_image_with_bands(["SR_B4", "SR_B5", "SR_B3"])

    assert calls["constant_arg"] == [0, 0, 0]


def test_renames_to_requested_bands(monkeypatch):
    captured = {}
    import ee

    def fake_constant(value):
        img = MagicMock(name="constant_img")
        def fake_rename(bands):
            captured["rename_arg"] = bands
            renamed = MagicMock(name="renamed")
            renamed.updateMask.return_value = renamed
            return renamed
        img.rename.side_effect = fake_rename
        return img

    monkeypatch.setattr(ee.Image, "constant", staticmethod(fake_constant))

    layers._empty_image_with_bands(["SR_B4", "SR_B5", "SR_B3"])

    assert captured["rename_arg"] == ["SR_B4", "SR_B5", "SR_B3"]


def test_applies_fully_transparent_mask(monkeypatch):
    captured = {}
    import ee

    def fake_constant(value):
        img = MagicMock()
        def fake_rename(bands):
            renamed = MagicMock()
            def fake_update_mask(mask):
                captured["mask_arg"] = mask
                return renamed
            renamed.updateMask.side_effect = fake_update_mask
            return renamed
        img.rename.side_effect = fake_rename
        return img

    monkeypatch.setattr(ee.Image, "constant", staticmethod(fake_constant))

    layers._empty_image_with_bands(["SR_B4"])

    assert captured["mask_arg"] == 0, "empty image deve ser totalmente transparente (alpha=0)"


def test_single_band():
    img = layers._empty_image_with_bands(["single"])
    assert img is not None


def test_empty_bands_list_is_invalid():
    with pytest.raises((ValueError, IndexError, AssertionError)):
        layers._empty_image_with_bands([])
