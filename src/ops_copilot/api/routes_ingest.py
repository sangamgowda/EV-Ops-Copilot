"""/ingest — Lane B document ingestion over HTTP.

Upload one file (markdown, text or PDF). Front matter supplies the
metadata; form fields override it. Needs the admin token: what is
ingested is what the agent reads. Re-uploading unchanged content is
a no-op (`skipped_duplicate`); uploading an edited file with the same
name replaces the previous version.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ops_copilot.api.schemas import IngestResponse
from ops_copilot.api.security import require_admin
from ops_copilot.rag.ingest import (
    DOMAINS,
    SUPPORTED_SUFFIXES,
    TRUST_LEVELS,
    ingest_text,
    read_document,
)

router = APIRouter(dependencies=[Depends(require_admin)])

MAX_BYTES = 20 * 1024 * 1024


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI's declared idiom
    domain: str | None = Form(default=None),
    doc_type: str | None = Form(default=None),
    trust_level: str | None = Form(default=None),
    uploaded_by: str = Form(default="api"),
) -> IngestResponse:
    name = Path(file.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(415, f"unsupported file type {suffix!r}; expected {SUPPORTED_SUFFIXES}")
    if domain is not None and domain not in DOMAINS:
        raise HTTPException(422, f"domain must be one of {DOMAINS}")
    if trust_level is not None and trust_level not in TRUST_LEVELS:
        raise HTTPException(422, f"trust_level must be one of {TRUST_LEVELS}")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large")

    if suffix == ".pdf":
        # pypdf reads from a path; the temp file lives only for the parse.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            raw = await asyncio.to_thread(read_document, path)
    else:
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "file is not UTF-8 text") from exc

    try:
        result = await ingest_text(raw, Path(name).stem, domain=domain, doc_type=doc_type,
                                   trust_level=trust_level, uploaded_by=uploaded_by)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return IngestResponse(**result)
