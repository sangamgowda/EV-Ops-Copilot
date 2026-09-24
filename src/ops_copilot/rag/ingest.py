"""Lane B — document ingestion. Rows and JSON do not come through here.

There are TWO pipelines, not two stages of one.

  Lane A  rows and JSON. Synced/replicated from the source database
          as-is. No chunking, no embedding, nothing to process — a
          row is already usable the moment it lands. The sync IS
          the whole operation. See scripts/ and db/migrations/.

  Lane B  documents. This module. The fetch and the processing are
          the same act; there is no raw copy sitting in between.

Lane B, in order:

  1. read document
  2. content_hash -> already in `documents`? STOP. Nothing else runs.
  3. parse structure (headings, tables, lists)
  4. PROMOTE error-code tables into the error_codes table — real
     rows, not chunks. An exact code lookup should be a SQL match.
     This is the step that makes "ERR_401" reliable instead of
     approximately right.
  5. chunk the rest, structure-aware
  6. embed
  7. tag with domain, doc_type, applies_to_models, effective_date
  8. write chunks + record the hash

On step 5: the promoted table is still chunked. The SQL row is the
authority for an exact code; the row chunks exist for the question
that describes a fault without naming its code ("what does it mean
when the BMS times out"), which only retrieval can answer.

Embedding (step 6) runs before the write transaction opens, so a
slow CPU pass never holds locks. Steps 4 and 8 then commit together:
a document is either fully ingested — codes, chunks and hash — or
not at all. A half-written document would be skipped as a duplicate
on the next run and never repaired.

Same doc_id with a new hash is an update: the old version's chunks
and promoted codes are removed in the same transaction.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from ops_copilot.rag.chunking import (
    Block,
    Chunk,
    chunk_blocks,
    find_error_codes,
    parse_structure,
)
from ops_copilot.rag.embeddings import embed_documents
from ops_copilot.rag.hashing import content_hash

DOMAINS = ("diagnostic", "business")
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".pdf")

# Header spellings seen in real code tables, mapped onto error_codes.
_CODE_COLUMNS = {
    "code": "code", "error_code": "code", "dtc": "code", "trouble_code": "code",
    "subsystem": "subsystem", "system": "subsystem", "component": "subsystem",
    "meaning": "meaning", "description": "meaning",
    "recommended_action": "recommended_action", "action": "recommended_action",
    "severity": "severity", "level": "severity",
}


# ── reading ──────────────────────────────────────────────────

def split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, flags=re.DOTALL)
    if not m:
        return {}, raw
    meta = yaml.safe_load(m.group(1)) or {}
    return (meta if isinstance(meta, dict) else {}), raw[m.end():]


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    # PDF text extraction loses table structure; what comes back is
    # treated as prose. Tables that matter should arrive as markdown.
    pages = [p.extract_text() or "" for p in PdfReader(str(path)).pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return _read_pdf(path)
    return path.read_text(encoding="utf-8")


# ── promotion ────────────────────────────────────────────────

def _column_key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def error_code_rows(blocks: list[Block], source_document: str) -> list[dict[str, str | None]]:
    """Rows for the error_codes table, from any table shaped like one.

    A table qualifies by its columns (a code and a meaning), not by
    which document it sits in — a service bulletin that adds a code
    gets it promoted too.
    """
    out: dict[str, dict[str, str | None]] = {}
    for b in blocks:
        if b.kind != "table":
            continue
        cols = [_CODE_COLUMNS.get(_column_key(h)) for h in b.headers]
        if "code" not in cols or "meaning" not in cols:
            continue
        for row in b.rows:
            rec = {c: v for c, v in zip(cols, row, strict=False) if c and v}
            code = rec.get("code", "")
            if find_error_codes(code) != [code]:
                continue
            out[code] = {
                "code": code,
                "subsystem": rec.get("subsystem"),
                "meaning": rec["meaning"],
                "recommended_action": rec.get("recommended_action"),
                "severity": rec.get("severity"),
                "source_document": source_document,
            }
    return list(out.values())


# ── metadata ─────────────────────────────────────────────────

def _as_date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(x) for x in v]


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ── pipeline ─────────────────────────────────────────────────

async def ingest_text(
    raw: str,
    doc_id: str,
    *,
    domain: str | None = None,
    doc_type: str | None = None,
    title: str | None = None,
    applies_to_models: list[str] | None = None,
    effective_date: date | None = None,
) -> dict[str, Any]:
    """Ingest one document's text. Arguments override front matter."""
    from ops_copilot.db.engine import owner_engine

    engine = owner_engine()
    digest = content_hash(raw)

    # Step 2 — the cheap exit. A re-sync of an unchanged folder costs
    # one SELECT per file and nothing else.
    async with engine.connect() as conn:
        existing = (await conn.execute(
            text("SELECT doc_id FROM documents WHERE content_hash = :h"), {"h": digest},
        )).scalar()
    if existing:
        return {"doc_id": existing, "status": "skipped_duplicate", "chunks": 0, "error_codes_promoted": 0}

    meta, body = split_front_matter(raw)
    domain = domain or meta.get("domain")
    if domain not in DOMAINS:
        # Domain is the hard retrieval filter. A document without one
        # would be stored and then never found — fail loudly instead.
        raise ValueError(f"{doc_id}: domain must be one of {DOMAINS}, got {domain!r}")
    doc_type = doc_type or meta.get("doc_type") or "document"
    title = title or meta.get("title") or doc_id
    models = applies_to_models if applies_to_models is not None else _as_list(meta.get("applies_to_models"))
    effective = effective_date or _as_date(meta.get("effective_date"))

    blocks = parse_structure(body)
    codes = error_code_rows(blocks, doc_id)
    chunks: list[Chunk] = chunk_blocks(blocks, title)
    if not chunks:
        raise ValueError(f"{doc_id}: no content to index")
    vectors = await asyncio.to_thread(embed_documents, [c.content for c in chunks])

    async with engine.begin() as conn:
        # Another worker may have ingested the same content while we
        # were embedding; the UNIQUE constraint would reject us anyway,
        # this just turns that into a clean skip.
        if (await conn.execute(
            text("SELECT 1 FROM documents WHERE content_hash = :h"), {"h": digest},
        )).scalar():
            return {"doc_id": doc_id, "status": "skipped_duplicate", "chunks": 0, "error_codes_promoted": 0}

        replaced = (await conn.execute(
            text("DELETE FROM documents WHERE doc_id = :d RETURNING doc_id"), {"d": doc_id},
        )).scalar() is not None
        await conn.execute(text("DELETE FROM error_codes WHERE source_document = :d"), {"d": doc_id})

        await conn.execute(
            text("""
                INSERT INTO documents (doc_id, content_hash, title, doc_type, domain,
                                       applies_to_models, effective_date)
                VALUES (:doc_id, :h, :title, :doc_type, :domain, :models, :eff)
            """),
            {"doc_id": doc_id, "h": digest, "title": title, "doc_type": doc_type,
             "domain": domain, "models": models, "eff": effective},
        )

        if codes:
            await conn.execute(
                text("""
                    INSERT INTO error_codes (code, subsystem, meaning, recommended_action,
                                             severity, source_document)
                    VALUES (:code, :subsystem, :meaning, :recommended_action,
                            :severity, :source_document)
                    ON CONFLICT (code) DO UPDATE SET
                        subsystem = EXCLUDED.subsystem,
                        meaning = EXCLUDED.meaning,
                        recommended_action = EXCLUDED.recommended_action,
                        severity = EXCLUDED.severity,
                        source_document = EXCLUDED.source_document
                """),
                codes,
            )

        await conn.execute(
            text("""
                INSERT INTO document_chunks (doc_id, chunk_type, section_path, content,
                                             error_codes, embedding)
                VALUES (:doc_id, :chunk_type, :section_path, :content, :error_codes,
                        CAST(:embedding AS vector))
            """),
            [
                {"doc_id": doc_id, "chunk_type": c.chunk_type, "section_path": c.section_path,
                 "content": c.content, "error_codes": c.error_codes,
                 "embedding": _vector_literal(v)}
                for c, v in zip(chunks, vectors, strict=False)
            ],
        )

    return {
        "doc_id": doc_id,
        "status": "replaced" if replaced else "ingested",
        "title": title,
        "domain": domain,
        "chunks": len(chunks),
        "error_codes_promoted": len(codes),
    }


async def ingest_document(path: Path, domain: str | None = None, doc_type: str | None = None) -> dict:
    """Ingest a file. doc_id is the file stem, so re-ingesting an
    edited file replaces the old version rather than adding a copy."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"{path.name}: unsupported type; expected one of {SUPPORTED_SUFFIXES}")
    raw = await asyncio.to_thread(read_document, path)
    return await ingest_text(raw, path.stem, domain=domain, doc_type=doc_type)


async def ingest_directory(directory: Path, domain: str | None = None) -> list[dict]:
    """Every supported file, one at a time. One bad file is reported,
    not allowed to abort the rest of the folder."""
    results: list[dict] = []
    for path in sorted(Path(directory).iterdir()):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            results.append(await ingest_document(path, domain=domain))
        except Exception as exc:  # reported per file, by design
            results.append({"doc_id": path.stem, "status": "failed", "error": str(exc)})
    return results
