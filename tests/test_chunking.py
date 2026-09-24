"""Chunking tests. The table cases are the point of this module."""

from __future__ import annotations

from pathlib import Path

from ops_copilot.rag.chunking import chunk_document, parse_structure
from ops_copilot.rag.ingest import split_front_matter

DOCS = Path(__file__).resolve().parents[1] / "data" / "documents"

CODE_TABLE = """# Codes

## Trouble code table

| Code | Subsystem | Meaning | Recommended action | Severity |
|---|---|---|---|---|
| ERR_401 | BMS | BMS communication timeout | Reseat BMS connector | warning |
| ERR_402 | BMS | Cell voltage imbalance | Run a balance charge | warning |
| ERR_601 | Controller | Sustained over-current | Check payload | warning |
"""


def _types(chunks):
    return [c.chunk_type for c in chunks]


class TestTablesAreNeverSplit:
    def test_wide_table_stays_one_chunk(self):
        rows = "\n".join(
            f"| Mode {i} | {'a long descriptive value ' * 6} | {i * 10} |" for i in range(40)
        )
        doc = f"# Doc\n\n## Specs\n\n| Mode | Notes | Value |\n|---|---|---|\n{rows}\n"
        chunks = chunk_document(doc, "Doc", "manual")

        tables = [c for c in chunks if c.chunk_type == "table"]
        assert len(tables) == 1
        # Far over prose_chunk_size, and still whole.
        assert len(tables[0].content) > 4000
        assert "Mode 0 " in tables[0].content and "Mode 39 " in tables[0].content
        # "Mode N" is not key-shaped, so no row chunks either.
        assert "table_row" not in _types(chunks)

    def test_lookup_table_emits_row_chunks(self):
        chunks = chunk_document(CODE_TABLE, "Codes", "manual")
        assert _types(chunks).count("table") == 1
        rows = [c for c in chunks if c.chunk_type == "table_row"]
        assert [r.metadata["row_key"] for r in rows] == ["ERR_401", "ERR_402", "ERR_601"]

    def test_row_chunk_repeats_headers(self):
        row = next(c for c in chunk_document(CODE_TABLE, "Codes", "manual")
                   if c.chunk_type == "table_row")
        # Read alone, the row still says which value is which.
        assert "Code: ERR_401" in row.content
        assert "Recommended action: Reseat BMS connector" in row.content
        assert "Severity: warning" in row.content
        assert row.error_codes == ["ERR_401"]

    def test_error_codes_extracted_for_promotion(self):
        from ops_copilot.rag.ingest import error_code_rows

        rows = error_code_rows(parse_structure(CODE_TABLE), "codes_doc")
        assert {r["code"] for r in rows} == {"ERR_401", "ERR_402", "ERR_601"}
        r401 = next(r for r in rows if r["code"] == "ERR_401")
        assert r401 == {
            "code": "ERR_401",
            "subsystem": "BMS",
            "meaning": "BMS communication timeout",
            "recommended_action": "Reseat BMS connector",
            "severity": "warning",
            "source_document": "codes_doc",
        }

    def test_table_chunk_carries_all_codes(self):
        table = next(c for c in chunk_document(CODE_TABLE, "Codes", "manual")
                     if c.chunk_type == "table")
        assert table.error_codes == ["ERR_401", "ERR_402", "ERR_601"]

    def test_short_caption_moves_into_table_chunk(self):
        doc = ("# D\n\n## Limits\n\nRated limits per model:\n\n"
               "| Model | Payload |\n|---|---|\n| M-50 | 150 |\n")
        chunks = chunk_document(doc, "D", "manual")
        assert _types(chunks) == ["table", "table_row"]
        assert "Rated limits per model:" in chunks[0].content
        # Not emitted a second time as its own prose chunk.
        assert sum("Rated limits" in c.content for c in chunks) == 1


class TestProse:
    def _two_sections(self) -> str:
        a = " ".join(f"alpha sentence number {i} about charging." for i in range(60))
        b = " ".join(f"bravo sentence number {i} about braking." for i in range(60))
        return f"# Manual\n\n## Charging\n\n{a}\n\n## Braking\n\n{b}\n"

    def test_never_spans_a_heading(self):
        chunks = chunk_document(self._two_sections(), "Manual", "manual")
        assert len(chunks) > 2
        for c in chunks:
            assert not ("alpha" in c.content and "bravo" in c.content)
            expected = "alpha" if c.section_path == "Charging" else "bravo"
            assert expected in c.content

    def test_overlap_applied(self):
        chunks = [c for c in chunk_document(self._two_sections(), "Manual", "manual")
                  if c.section_path == "Charging"]
        assert len(chunks) >= 2
        first_body = chunks[0].content.split("\n\n", 1)[1]
        second_body = chunks[1].content.split("\n\n", 1)[1]
        # The second chunk opens with the end of the first.
        assert second_body[:40] in first_body
        assert chunks[1].metadata["overlap_chars"] > 0
        # The first chunk of a section has nothing to overlap with.
        assert chunks[0].metadata["overlap_chars"] == 0

    def test_prose_respects_chunk_size(self):
        for c in chunk_document(self._two_sections(), "Manual", "manual"):
            body = c.content.split("\n\n", 1)[1]
            assert len(body) <= 800 + 120 + 1

    def test_context_header_prepended(self):
        chunks = chunk_document(self._two_sections(), "Manual", "manual")
        assert chunks[0].content.startswith("Manual > Charging\n\n")
        # The H1 repeats the title; it is not doubled in the path.
        assert chunks[0].section_path == "Charging"

    def test_list_chunk_type(self):
        doc = "# D\n\n## Severity levels\n\n- **info** — none.\n- **warning** — book a visit.\n"
        chunks = chunk_document(doc, "D", "manual")
        assert _types(chunks) == ["list"]


class TestRealDocuments:
    def test_every_document_chunks(self):
        for path in DOCS.glob("*.md"):
            meta, body = split_front_matter(path.read_text(encoding="utf-8"))
            chunks = chunk_document(body, meta["title"], meta["doc_type"])
            assert chunks, path.name
            assert all(c.content.startswith(meta["title"]) for c in chunks), path.name

    def test_service_manual_code_table(self):
        meta, body = split_front_matter((DOCS / "manual_error_codes.md").read_text(encoding="utf-8"))
        chunks = chunk_document(body, meta["title"], meta["doc_type"])
        rows = [c for c in chunks if c.chunk_type == "table_row"]
        assert len(rows) == 16
        assert any(r.error_codes == ["ERR_401"] for r in rows)
