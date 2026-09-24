"""Ingestion tests for the parts that run before the database:
front matter, hashing, and error-code promotion."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

from ops_copilot.rag.chunking import parse_structure
from ops_copilot.rag.hashing import content_hash
from ops_copilot.rag.ingest import error_code_rows, split_front_matter

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "data" / "documents" / "manual_error_codes.md"


class TestFrontMatter:
    def test_parsed(self):
        meta, body = split_front_matter(MANUAL.read_text(encoding="utf-8"))
        assert meta["domain"] == "diagnostic"
        assert meta["applies_to_models"] == ["SC-F50", "SC-F45", "SC-F50G1"]
        assert meta["effective_date"] == date(2026, 1, 15)
        assert body.lstrip().startswith("# Service Manual")

    def test_absent(self):
        assert split_front_matter("# Just a doc\n") == ({}, "# Just a doc\n")


class TestHashing:
    def test_line_endings_and_trailing_space_do_not_change_hash(self):
        assert content_hash("a  b\r\nc\n\n\n\n") == content_hash("a b\nc")

    def test_real_edit_changes_hash(self):
        assert content_hash("ERR_401") != content_hash("err_401")


class TestPromotion:
    def test_matches_seed_script(self, monkeypatch):
        """The seed script parses the same table independently. Both
        write error_codes; they must agree row for row."""
        spec = importlib.util.spec_from_file_location("seed", ROOT / "scripts" / "seed_synthetic_data.py")
        seed = importlib.util.module_from_spec(spec)
        # Its dataclasses resolve annotations through sys.modules.
        monkeypatch.setitem(sys.modules, "seed", seed)
        spec.loader.exec_module(seed)

        _, body = split_front_matter(MANUAL.read_text(encoding="utf-8"))
        ours = error_code_rows(parse_structure(body), MANUAL.stem)
        theirs = seed.parse_error_codes(MANUAL)
        assert sorted(ours, key=lambda r: r["code"]) == sorted(theirs, key=lambda r: r["code"])
        assert len(ours) == 14

    def test_table_without_meaning_column_not_promoted(self):
        doc = "| Code | Count |\n|---|---|\n| ERR_401 | 12 |\n"
        assert error_code_rows(parse_structure(doc), "d") == []

    def test_non_code_values_skipped(self):
        doc = "| Code | Meaning |\n|---|---|\n| ERR_401 | Timeout |\n| see below | n/a |\n"
        assert [r["code"] for r in error_code_rows(parse_structure(doc), "d")] == ["ERR_401"]
