"""Testes das funções puras de `scripts/purge_poisoned_tiles` (PR #2)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "purge_poisoned_tiles",
    ROOT / "scripts" / "purge_poisoned_tiles.py",
)
purge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(purge)


class TestIsPoisoned:
    def test_size_below_threshold_is_poisoned(self):
        assert purge.is_poisoned({"size": "334"}, threshold_bytes=1024) is True

    def test_size_at_threshold_is_not_poisoned(self):
        assert purge.is_poisoned({"size": "1024"}, threshold_bytes=1024) is False

    def test_size_above_threshold_is_not_poisoned(self):
        assert purge.is_poisoned({"size": "5000"}, threshold_bytes=1024) is False

    def test_missing_size_is_not_poisoned(self):
        """Sem campo size → não é seguro assumir envenenado. Mantemos."""
        assert purge.is_poisoned({}, threshold_bytes=1024) is False

    def test_non_numeric_size_is_not_poisoned(self):
        """Corrompido — preferimos não deletar."""
        assert purge.is_poisoned({"size": "abc"}, threshold_bytes=1024) is False

    def test_accepts_int_size(self):
        """size pode vir como int direto (sem conversão Redis)."""
        assert purge.is_poisoned({"size": 334}, threshold_bytes=1024) is True

    def test_accepts_bytes_size(self):
        """Redis com decode_responses=False pode devolver bytes."""
        assert purge.is_poisoned({"size": b"334"}, threshold_bytes=1024) is True

    def test_size_string_zero_is_poisoned(self):
        """Bug real: `meta.get('size') or ...` tratava '0' como falsy."""
        assert purge.is_poisoned({"size": "0"}, threshold_bytes=1024) is True

    def test_size_int_zero_is_poisoned(self):
        """Bug real: 0 é falsy no `or`, mas 0 bytes é o envenenado mais claro."""
        assert purge.is_poisoned({"size": 0}, threshold_bytes=1024) is True

    def test_size_bytes_zero_is_poisoned(self):
        assert purge.is_poisoned({"size": b"0"}, threshold_bytes=1024) is True


class TestExtractS3KeyEdgeCases:
    def test_empty_string_s3_key_returns_empty_not_none(self):
        """`or` tratava '' como ausente e fazia fallback silencioso."""
        assert purge.extract_s3_key({"s3_key": ""}) == ""

    def test_prefers_str_key_over_bytes_key_when_both_present(self):
        meta = {"s3_key": "tiles/a.png", b"s3_key": b"tiles/b.png"}
        assert purge.extract_s3_key(meta) == "tiles/a.png"


class TestExtractS3Key:
    def test_extracts_s3_key_from_metadata(self):
        meta = {"s3_key": "tiles/eb/landsat_WET_2006.../13/3045_4224.png"}
        assert purge.extract_s3_key(meta) == "tiles/eb/landsat_WET_2006.../13/3045_4224.png"

    def test_returns_none_when_missing(self):
        assert purge.extract_s3_key({}) is None

    def test_decodes_bytes(self):
        meta = {"s3_key": b"tiles/xx.png"}
        assert purge.extract_s3_key(meta) == "tiles/xx.png"
