"""Structure-aware chunking.

One strategy across all content is wrong, and tables are why.
Recursive character splitting shreds a table across boundaries, so
an error-code table becomes a set of fragments that answer nothing.

Rules:

  tables      never split. One table = one chunk, with its caption
              and nearest heading prepended so it reads standalone.

  lookup      ALSO emit one chunk per row, headers repeated:
  tables      "ERR_401 | BMS | comms timeout | Action: reseat..."
              An exact code then lands on a small precise chunk,
              which is where BM25 is strongest.

  prose       recursive split with overlap, but never across a
              heading boundary — a chunk spanning two sections
              answers neither well.

  all chunks  get a one-line context header prepended before
              embedding (document title + section path). Small
              ingestion-time cost, meaningful recall gain.

The stronger move for error codes is upstream of this file
entirely: promote them out of RAG into real Postgres rows. See
rag/ingest.py. Semantic search is the wrong tool for exact-key
lookup.

Public surface:
  parse_structure(text)          markdown -> ordered Blocks
  chunk_blocks(blocks, title)    Blocks -> Chunks
  chunk_document(text, ...)      both, for callers with raw text

Structure is parsed once and shared: ingest reads the same Blocks
for error-code promotion, so promotion and chunking can never
disagree about where a table starts and ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from markdown_it import MarkdownIt

from ops_copilot.settings import get_config

ChunkType = Literal["prose", "table", "table_row", "list"]
BlockKind = Literal["paragraph", "table", "list", "code"]

# Tried in order when a block is too big to fit one chunk. Paragraph
# breaks first, words last, so a split lands on the most natural
# boundary available.
_SEPARATORS = ("\n\n", "\n", ". ", " ")


@dataclass
class Chunk:
    content: str
    chunk_type: ChunkType
    section_path: str = ""
    error_codes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Block:
    kind: BlockKind
    text: str
    section: tuple[str, ...] = ()
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


# ── parsing ──────────────────────────────────────────────────

def _cfg() -> dict[str, Any]:
    return get_config()["chunking"]


def _parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    header = cells(lines[0])
    rows = [
        cells(line) for line in lines[1:]
        # The |---|:--:| delimiter row carries no data.
        if line.strip() and not set(line.strip()) <= set("|-: ")
    ]
    return header, rows


def parse_structure(text: str) -> list[Block]:
    """Markdown -> content blocks, each tagged with its heading path.

    markdown-it gives source line ranges for every top-level block,
    so blocks are sliced from the original text rather than
    re-rendered — tables keep their exact pipe layout.
    """
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(text)
    lines = text.splitlines()

    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []  # (heading level, heading text)

    for i, tok in enumerate(tokens):
        if tok.level != 0 or tok.map is None or tok.nesting == -1:
            continue
        start, end = tok.map
        raw = "\n".join(lines[start:end]).strip()
        section = tuple(h for _, h in stack)

        if tok.type == "heading_open":
            level = int(tok.tag[1:])
            heading = tokens[i + 1].content.strip()
            stack = [(lv, h) for lv, h in stack if lv < level]
            stack.append((level, heading))
        elif tok.type == "table_open":
            headers, rows = _parse_table(lines[start:end])
            blocks.append(Block("table", raw, section, headers, rows))
        elif tok.type in ("bullet_list_open", "ordered_list_open"):
            blocks.append(Block("list", raw, section))
        elif tok.type in ("fence", "code_block"):
            blocks.append(Block("code", raw, section))
        elif tok.type in ("paragraph_open", "blockquote_open", "html_block"):
            if raw:
                blocks.append(Block("paragraph", raw, section))
        # hr and anything else carries no retrievable content.

    return blocks


# ── helpers ──────────────────────────────────────────────────

def _section_path(section: tuple[str, ...], title: str) -> str:
    # The H1 almost always repeats the title; keeping it would put
    # the title into every context header twice.
    parts = list(section)
    if parts and parts[0].strip().casefold() == title.strip().casefold():
        parts = parts[1:]
    return " > ".join(parts)


def _context_header(title: str, section_path: str) -> str:
    return f"{title} > {section_path}" if section_path else title


def _with_header(body: str, title: str, section_path: str) -> str:
    if not _cfg().get("prepend_context_header", True):
        return body
    return f"{_context_header(title, section_path)}\n\n{body}"


def find_error_codes(text: str) -> list[str]:
    pattern = re.compile(_cfg()["error_code_pattern"])
    return list(dict.fromkeys(pattern.findall(text)))


def is_lookup_table(block: Block) -> bool:
    """Every first-column value is a distinct key-shaped identifier."""
    if not block.rows:
        return False
    keys = [r[0] for r in block.rows if r]
    key_re = re.compile(_cfg()["lookup_key_pattern"])
    return (
        len(keys) == len(block.rows)
        and len(set(keys)) == len(keys)
        and all(key_re.match(k) for k in keys)
    )


def _row_text(headers: list[str], row: list[str]) -> str:
    # Headers repeated per row: a row chunk read alone has to say
    # which value is the action and which is the severity.
    pairs = [f"{h}: {v}" for h, v in zip(headers, row, strict=False) if v]
    return " | ".join(pairs)


def _split_recursive(text: str, size: int, seps: tuple[str, ...] = _SEPARATORS) -> list[str]:
    if len(text) <= size:
        return [text]
    if not seps:
        return [text[i:i + size] for i in range(0, len(text), size)]

    sep, rest = seps[0], seps[1:]
    parts = text.split(sep)
    if len(parts) == 1:
        return _split_recursive(text, size, rest)

    out: list[str] = []
    current = ""
    for part in parts:
        piece = part if not current else current + sep + part
        if len(piece) <= size:
            current = piece
            continue
        if current:
            out.append(current)
        if len(part) > size:
            out.extend(_split_recursive(part, size, rest))
            current = ""
        else:
            current = part
    if current:
        out.append(current)
    return out


def _tail(text: str, n: int) -> str:
    """Last ~n chars, starting on a word boundary."""
    if n <= 0 or len(text) <= n:
        return text if n > 0 else ""
    tail = text[-n:]
    space = tail.find(" ")
    return tail[space + 1:] if 0 <= space < len(tail) - 1 else tail


# ── chunking ─────────────────────────────────────────────────

def _table_chunks(block: Block, caption: str | None, title: str) -> list[Chunk]:
    cfg = _cfg()
    path = _section_path(block.section, title)
    body = f"{caption}\n\n{block.text}" if caption else block.text
    lookup = is_lookup_table(block)

    chunks = [Chunk(
        content=_with_header(body, title, path),
        chunk_type="table",
        section_path=path,
        error_codes=find_error_codes(block.text),
        metadata={"headers": block.headers, "row_count": len(block.rows), "lookup": lookup},
    )]

    if lookup and cfg.get("emit_row_chunks_for_lookup_tables", True):
        for row in block.rows:
            text = _row_text(block.headers, row)
            chunks.append(Chunk(
                content=_with_header(text, title, path),
                chunk_type="table_row",
                section_path=path,
                error_codes=find_error_codes(text),
                metadata={"row_key": row[0]},
            ))
    return chunks


def _prose_chunks(blocks: list[Block], title: str) -> list[Chunk]:
    """Pack one section's non-table blocks into overlapping chunks."""
    cfg = _cfg()
    size, overlap = cfg["prose_chunk_size"], cfg["prose_overlap"]
    path = _section_path(blocks[0].section, title)

    # Greedy packing of whole blocks; only a block that cannot fit
    # on its own gets split internally.
    pieces: list[tuple[str, bool]] = []  # (text, is_list)
    current, current_all_lists = "", True
    for b in blocks:
        for part in _split_recursive(b.text, size):
            candidate = f"{current}\n\n{part}" if current else part
            if current and len(candidate) > size:
                pieces.append((current, current_all_lists))
                current, current_all_lists = part, b.kind == "list"
            else:
                current = candidate
                current_all_lists = current_all_lists and b.kind == "list"
    if current:
        pieces.append((current, current_all_lists))

    chunks: list[Chunk] = []
    prev = ""
    for text, all_lists in pieces:
        # Overlap never crosses a heading: `prev` resets per section
        # because this function only ever sees one section.
        body = f"{_tail(prev, overlap)} {text}".strip() if prev else text
        chunks.append(Chunk(
            content=_with_header(body, title, path),
            chunk_type="list" if all_lists else "prose",
            section_path=path,
            error_codes=find_error_codes(body),
            metadata={"overlap_chars": len(body) - len(text)},
        ))
        prev = text
    return chunks


def chunk_blocks(blocks: list[Block], title: str) -> list[Chunk]:
    cfg = _cfg()
    chunks: list[Chunk] = []
    pending: list[Block] = []

    def flush() -> None:
        if pending:
            chunks.extend(_prose_chunks(pending, title))
            pending.clear()

    for i, block in enumerate(blocks):
        if pending and pending[0].section != block.section:
            flush()
        if block.kind != "table":
            pending.append(block)
            continue

        # A short paragraph directly above a table, in the same
        # section, is its caption. It moves into the table chunk so
        # the table reads standalone — and is not emitted twice.
        caption = None
        prev = blocks[i - 1] if i else None
        if (
            prev is not None
            and prev.kind == "paragraph"
            and prev.section == block.section
            and len(prev.text) <= cfg["max_caption_chars"]
            and pending and pending[-1] is prev
        ):
            caption = pending.pop().text
        flush()
        chunks.extend(_table_chunks(block, caption, title))

    flush()
    return chunks


def chunk_document(text: str, title: str, doc_type: str) -> list[Chunk]:
    chunks = chunk_blocks(parse_structure(text), title)
    for c in chunks:
        c.metadata["doc_type"] = doc_type
    return chunks
