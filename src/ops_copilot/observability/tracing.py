"""Tracing. Every node is recorded as it runs; the turn is sent at the end.

Not reconstructed from logs afterwards: each node is wrapped, so its
input, output, latency and the tokens of every LLM call it makes are
captured as it executes. One trace per turn (id == turn_id, the id
feedback is given against), tagged with the session. Each lap of the
loop is its own span; plan / execute / observe / reflect nest inside it.

Why fields, not prose: "debug wrong tool selection across a million
requests" is answered by filtering — stop_reason = cap_reached,
grounded = false, tool = rag_retrieval_tool — clustering by pattern,
and reading one representative per cluster. So every trace carries
structured metadata and tags for exactly those filters, and a one-line
JSON summary of the same fields goes to the application log, which
works with or without Langfuse.

Recorded per turn: router output in full; per lap, plan reasoning and
tool calls with exact arguments (the SQL), each tool's verdict —
rejected with reasons, EXPLAIN cost, row count, retrieved chunks with
scores — and Reflect's verdict, gaps and stop reason; the answer with
citations; latency and tokens per node; prompt version hashes (without
them a past failure cannot be re-checked after a prompt change, which
makes the feedback loop unfalsifiable); a snapshot of the thresholds in
effect.

Production rules this module enforces:

  Summaries, not payloads. Row counts and short excerpts; long strings
  are cut to observability.max_field_chars. Full rows stay in
  evidence_raw.

  Never break a request. Every call into the tracing backend is
  wrapped; failures are logged and swallowed. Nothing is sent while
  the turn runs — the recording is assembled in memory and handed to
  an OpenTelemetry batch exporter (which ships it from a background
  thread) after the answer exists.

  Scrub secrets. Credentials in connection strings, API-key-shaped
  tokens, and the literal values of configured secrets are replaced
  with *** before anything leaves the process.

  Sample successes, keep every failure. Whether a turn failed is only
  known at its end — which is why recording is deferred. Failures are
  always sent; successes at observability.success_sample_rate.
  Trade-off: a process that dies mid-turn loses that turn's trace.
"""

from __future__ import annotations

import base64
import contextvars
import functools
import hashlib
import json
import logging
import os
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

from ops_copilot.settings import CONFIG_DIR, get_config, get_settings

log = logging.getLogger(__name__)
summary_log = logging.getLogger("ops_copilot.turns")

LOOP_NODES = ("plan", "execute", "observe", "reflect")


# ── scrubbing ────────────────────────────────────────────────

_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s/]+@", re.I)
_TOKEN_SHAPES = re.compile(
    r"\b(gsk_[A-Za-z0-9]{10,}|sk-lf-[A-Za-z0-9-]{8,}|pk-lf-[A-Za-z0-9-]{8,}|sk-[A-Za-z0-9]{20,})\b"
)


@functools.lru_cache(maxsize=1)
def _secret_values() -> tuple[str, ...]:
    s = get_settings()
    candidates = [s.groq_api_key, s.langfuse_secret_key, s.langfuse_public_key,
                  os.environ.get("POSTGRES_PASSWORD", ""), os.environ.get("DB_READONLY_PASSWORD", "")]
    for url in (s.database_url, s.database_url_readonly):
        m = re.search(r"://[^:/\s@]+:([^@\s/]+)@", url or "")
        if m:
            candidates.append(m.group(1))
    # Short values would match ordinary words; real secrets are longer.
    return tuple(sorted({c for c in candidates if c and len(c) >= 6}, key=len, reverse=True))


def scrub(value: Any) -> Any:
    """Replace secrets anywhere in a (nested) value with ***."""
    if isinstance(value, str):
        text = _URL_CREDENTIALS.sub(r"\1***@", value)
        text = _TOKEN_SHAPES.sub("***", text)
        for secret in _secret_values():
            text = text.replace(secret, "***")
        return text
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    return value


def _cut(value: Any, limit: int | None = None) -> Any:
    limit = limit or get_config()["observability"]["max_field_chars"]
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, dict):
        return {k: _cut(v, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_cut(v, limit) for v in value]
    return value


def _jsonable(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "value") and not isinstance(v, str | int | float | bool):   # enums
        return v.value
    return v


def _clean(v: Any) -> Any:
    return _cut(scrub(_jsonable(v)))


# ── what each node records ───────────────────────────────────
# Projections, not dumps: what a person debugging this node needs, and
# nothing that makes a trace unreadable or expensive.

def _evidence_view(e: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": e.id, "tool": e.tool, "status": e.status.value,
                           "summary": e.summary[:300]}
    if e.verdict is not None:
        out.update(metric=e.metric, actual=e.actual, baseline=e.baseline,
                   delta_pct=e.delta_pct, verdict=e.verdict.value)
    if e.source_doc:
        out.update(source_doc=e.source_doc, chunk_ids=e.chunk_ids, rerank_score=e.rerank_score)
    if e.error:
        out["error"] = e.error
    return out


def _tool_result_view(raw: dict[str, Any]) -> dict[str, Any]:
    res = raw.get("result", {})
    view: dict[str, Any] = {"tool": raw.get("tool"), "args": raw.get("args"),
                            "status": res.get("status"), "latency_ms": raw.get("latency_ms")}
    if raw.get("tool") == "structured_query_tool":
        view.update(sql_ran=res.get("sql"),
                    validation="rejected" if res.get("status") == "rejected" else "passed",
                    stage=res.get("stage"), reasons=res.get("reasons"), error=res.get("error"),
                    explain_cost=res.get("explain_cost"), row_count=res.get("row_count"),
                    warnings=res.get("warnings"))
    else:
        view.update(best_score=res.get("best_score"), threshold=res.get("threshold"),
                    candidates=res.get("candidate_count"), rerank_skipped=res.get("rerank_skipped"),
                    chunks=[{"chunk_id": c.get("chunk_id"), "doc": c.get("doc_id"),
                             "section": c.get("section_path"), "score": c.get("rerank_score")}
                            for c in res.get("chunks", [])],
                    entity_resolution=res.get("entity_resolution"), error=res.get("error"))
    return {k: v for k, v in view.items() if v is not None}


def _node_input(node: str, state: dict[str, Any]) -> dict[str, Any]:
    if node == "router":
        return {"question": state.get("question")}
    if node == "plan":
        return {"lap": state.get("iteration", 0) + 1, "next_question": state.get("next_question"),
                "evidence_so_far": len(state.get("evidence", []))}
    if node == "execute":
        return {"tool_calls": [c.model_dump() for c in state.get("pending_tool_calls", [])]}
    if node == "observe":
        return {"results": len(state.get("raw_results", []))}
    if node in ("reflect", "synthesize"):
        return {"lap": state.get("iteration"), "evidence": len(state.get("evidence", [])),
                "partial": state.get("partial")}
    if node == "grounding":
        return {"answer_chars": len(state.get("answer") or ""),
                "citations": len(state.get("citations", []))}
    return {}


def _node_output(node: str, out: dict[str, Any]) -> dict[str, Any]:
    if node == "router":
        return {k: out.get(k) for k in ("domains", "query_type", "complexity_hint", "entities",
                                        "resolved_entities", "entity_notes", "hypothesis_to_test")}
    if node == "plan":
        return {"reasoning": out.get("plan_reasoning"),
                "tool_calls": [c.model_dump() for c in out.get("pending_tool_calls", [])]}
    if node == "execute":
        return {"results": [_tool_result_view(r) for r in out.get("raw_results", [])]}
    if node == "observe":
        return {"evidence": [_evidence_view(e) for e in out.get("evidence", [])]}
    if node == "reflect":
        return {k: out.get(k) for k in ("stop_reason", "partial", "open_gaps", "next_question")}
    if node == "synthesize":
        return {"answer": out.get("answer"), "confidence": out.get("confidence"),
                "citations": out.get("citations"), "gaps": out.get("gaps")}
    if node == "grounding":
        return {"groundedness": out.get("groundedness")}
    return {}


# ── the in-memory recording ──────────────────────────────────

@dataclass
class _Obs:
    id: str
    kind: str                       # span | generation
    name: str
    parent_id: str | None
    start: datetime
    end: datetime | None = None
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    level: str = "DEFAULT"
    status_message: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class TurnRecording:
    turn_id: str
    session_id: str
    question: str
    start: datetime = field(default_factory=lambda: datetime.now(UTC))
    observations: list[_Obs] = field(default_factory=list)
    laps: dict[int, str] = field(default_factory=dict)       # lap -> span id
    metadata: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    node_errors: list[str] = field(default_factory=list)

    def lap_span(self, lap: int) -> str:
        if lap not in self.laps:
            obs = _Obs(id=uuid.uuid4().hex, kind="span", name=f"lap {lap}", parent_id=None,
                       start=datetime.now(UTC), metadata={"lap": lap})
            self.observations.append(obs)
            self.laps[lap] = obs.id
        return self.laps[lap]

    def extend_lap(self, lap: int, until: datetime) -> None:
        for o in self.observations:
            if o.id == self.laps.get(lap):
                o.end = until

    def tokens(self) -> dict[str, int]:
        total = {"input": 0, "output": 0}
        for o in self.observations:
            for k in total:
                total[k] += (o.usage or {}).get(k, 0)
        return total


_recording: contextvars.ContextVar[TurnRecording | None] = contextvars.ContextVar(
    "turn_recording", default=None)
_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span", default=None)


def _file_hash(name: str) -> str:
    return hashlib.sha256((CONFIG_DIR / name).read_bytes()).hexdigest()[:12]


def config_snapshot() -> dict[str, Any]:
    """The thresholds in effect, and hashes of the files they came from,
    so a trace says exactly which configuration produced it."""
    c, s = get_config(), get_settings()
    return {
        "app_config_hash": _file_hash("app_config.yaml"),
        "schema_config_hash": _file_hash("schema_config.yaml"),
        "max_iterations": c["agent"]["max_iterations"],
        "turn_timeout_seconds": c["api"]["turn_timeout_seconds"],
        "confidence_threshold": c["retrieval"]["confidence_threshold"],
        "candidate_k": c["retrieval"]["candidate_k"],
        "final_k": c["retrieval"]["final_k"],
        "rerank_skip_margin": c["retrieval"]["rerank_skip_margin"],
        "entity_boost": c["retrieval"]["entity_boost"],
        "explain_cost_budget": c["sql_validation"]["explain_cost_budget"],
        "statement_timeout_ms": c["sql_validation"]["statement_timeout_ms"],
        "models": {"cheap": s.llm_model_cheap, "strong": s.llm_model_strong},
        "embedding_model": s.embedding_model,
        "reranker_model": s.reranker_model,
    }


# ── public API used by the rest of the system ────────────────

@contextmanager
def turn_trace(turn_id: str, session_id: str, question: str) -> Generator[TurnRecording, None, None]:
    """Record one turn. Sent (or sampled out) when the block exits."""
    rec = TurnRecording(turn_id=turn_id, session_id=session_id, question=question)
    rec.metadata["config"] = _safe(config_snapshot) or {}
    token = _recording.set(rec)
    try:
        yield rec
    finally:
        _recording.reset(token)
        _safe(_finish, rec)


def update_turn(output: Any = None, **fields: Any) -> None:
    """Attach the turn's outcome: the filterable fields."""
    rec = _recording.get()
    if rec is not None:
        rec.output = output
        rec.metadata.update(fields)


def record_node_error(node: str, exc: BaseException) -> None:
    rec = _recording.get()
    if rec is not None:
        rec.node_errors.append(f"{node}: {type(exc).__name__}: {exc}")


def traced(node_name: str) -> Callable[[Callable[..., Awaitable[dict]]], Callable[..., Awaitable[dict]]]:
    """Wrap a graph node: record its input, output, latency and errors."""
    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(state: dict, config: Any = None) -> dict:
            rec = _recording.get()
            if rec is None:
                return await fn(state, config)

            lap = None
            if node_name in LOOP_NODES:
                lap = state.get("iteration", 0) + (1 if node_name == "plan" else 0)
            obs = _Obs(id=uuid.uuid4().hex, kind="span", name=node_name,
                       parent_id=rec.lap_span(lap) if lap is not None else None,
                       start=datetime.now(UTC),
                       input=_safe(_node_input, node_name, state))
            rec.observations.append(obs)
            token = _span.set(obs.id)
            started = time.perf_counter()
            try:
                out = await fn(state, config)
            except Exception as exc:
                obs.level, obs.status_message = "ERROR", f"{type(exc).__name__}: {exc}"
                raise
            finally:
                _span.reset(token)
                obs.end = datetime.now(UTC)
                obs.metadata["latency_ms"] = int((time.perf_counter() - started) * 1000)
                if lap is not None:
                    rec.extend_lap(lap, obs.end)
            obs.output = _safe(_node_output, node_name, out)
            versions = out.get("prompt_versions", {}) if isinstance(out, dict) else {}
            if node_name in versions:
                obs.metadata["prompt_version"] = versions[node_name]
            return out
        return wrapper
    return decorator


def record_generation(node: str, model: str, messages: list[dict], output: str,
                      usage: dict[str, int], latency_ms: int) -> None:
    """One LLM call, under the node span that made it. The system prompt
    is versioned and hashed elsewhere, so only its opening is kept."""
    rec = _recording.get()
    if rec is None:
        return
    end = datetime.now(UTC)
    start = datetime.fromtimestamp(end.timestamp() - latency_ms / 1000, UTC)
    trimmed = [{"role": m["role"],
                "content": (m["content"][:200] + "…" if m["role"] == "system"
                            and len(m["content"]) > 200 else m["content"])} for m in messages]
    rec.observations.append(_Obs(
        id=uuid.uuid4().hex, kind="generation", name=f"{node}.llm", parent_id=_span.get(),
        start=start, end=end, input=trimmed, output=output, model=model,
        usage=usage or None, metadata={"latency_ms": latency_ms}))


def shutdown() -> None:
    """Send whatever is still queued. Call once, at process exit."""
    otel = _client_state["otel"]
    if otel:
        _safe(otel.provider.force_flush, 5000)


# ── sending: OpenTelemetry to Langfuse ───────────────────────
# Traces go out as standard OpenTelemetry spans (OTLP over HTTP), the
# format Langfuse's current ingestion is built on — and one any other
# tracing backend also reads, which keeps the system vendor-neutral.
# Langfuse reads its own fields from span attributes (langfuse.*).

class _TraceIds(RandomIdGenerator):
    """Random ids, except that a turn's root span takes the turn's own id,
    so trace id == turn_id and feedback joins to its trace directly."""

    def __init__(self) -> None:
        self.next_trace_id: int | None = None

    def generate_trace_id(self) -> int:
        if self.next_trace_id is not None:
            return self.next_trace_id
        return super().generate_trace_id()


@dataclass
class _Otel:
    provider: TracerProvider
    tracer: Any
    ids: _TraceIds


_client_state: dict[str, Any] = {"otel": None, "failed": False}


def make_otel(exporter: SpanExporter, *, batch: bool = True) -> _Otel:
    ids = _TraceIds()
    provider = TracerProvider(resource=Resource.create({"service.name": "ops-copilot"}),
                              id_generator=ids)
    # The batch processor exports from a background thread: a slow or
    # unreachable backend never holds up a request.
    provider.add_span_processor(BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter))
    return _Otel(provider=provider, tracer=provider.get_tracer("ops_copilot"), ids=ids)


def _otel() -> _Otel | None:
    if _client_state["otel"] is None and not _client_state["failed"] and get_settings().tracing_configured:
        try:
            s = get_settings()
            token = base64.b64encode(f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()).decode()
            exporter = OTLPSpanExporter(
                endpoint=f"{s.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {token}", "x-langfuse-ingestion-version": "4"},
                timeout=10,
            )
            _client_state["otel"] = make_otel(exporter)
        except Exception:
            log.warning("trace exporter could not start; tracing disabled", exc_info=True)
            _client_state["failed"] = True
    return _client_state["otel"]


def _trace_id(turn_id: str) -> int:
    """The turn id as a 128-bit trace id: used as-is when it already is
    one (32 hex characters), otherwise derived from it stably."""
    if re.fullmatch(r"[0-9a-f]{32}", turn_id):
        return int(turn_id, 16)
    return int(hashlib.sha256(turn_id.encode()).hexdigest()[:32], 16)


def _json(value: Any) -> str:
    return json.dumps(_clean(value), default=str, ensure_ascii=False)


def _metadata_attrs(prefix: str, metadata: dict[str, Any]) -> dict[str, str]:
    # One attribute per key: Langfuse makes each a top-level, filterable
    # metadata field. Values are strings (JSON for anything structured).
    return {f"{prefix}.{k}": v if isinstance(v, str) else _json(v)
            for k, v in _clean(metadata).items() if v is not None}


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _export(rec: TurnRecording, otel: _Otel) -> None:
    trace_attrs: dict[str, Any] = {
        "langfuse.trace.name": "turn",
        "langfuse.session.id": rec.session_id,
        "langfuse.trace.tags": _tags(rec),
        "langfuse.trace.input": _json({"question": rec.question}),
        "langfuse.trace.output": _json(rec.output),
        "langfuse.observation.input": _json({"question": rec.question}),
        "langfuse.observation.output": _json(rec.output),
        **_metadata_attrs("langfuse.trace.metadata", rec.metadata),
    }
    otel.ids.next_trace_id = _trace_id(rec.turn_id)
    try:
        root = otel.tracer.start_span("turn", context=otel_context.Context(),
                                      start_time=_ns(rec.start), attributes=trace_attrs)
    finally:
        otel.ids.next_trace_id = None

    spans: dict[str, Any] = {}
    for o in rec.observations:            # parents are always recorded before children
        parent = spans.get(o.parent_id, root) if o.parent_id else root
        attrs: dict[str, Any] = {
            "langfuse.observation.type": o.kind,
            "langfuse.observation.level": o.level,
            "langfuse.observation.input": _json(o.input),
            "langfuse.observation.output": _json(o.output),
            **_metadata_attrs("langfuse.observation.metadata", o.metadata),
        }
        if o.status_message:
            attrs["langfuse.observation.status_message"] = scrub(o.status_message)
        if o.kind == "generation":
            attrs["langfuse.observation.model.name"] = o.model or ""
            if o.usage:
                attrs["langfuse.observation.usage_details"] = json.dumps(o.usage)
        spans[o.id] = otel.tracer.start_span(o.name, context=otel_trace.set_span_in_context(parent),
                                             start_time=_ns(o.start), attributes=attrs)
    for o in rec.observations:
        spans[o.id].end(end_time=_ns(o.end or o.start))
    root.end(end_time=_ns(datetime.now(UTC)))


def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.warning("tracing step failed (the request is unaffected)", exc_info=True)
        return None


def is_failure(rec: TurnRecording) -> bool:
    m = rec.metadata
    cfg = get_config()["observability"]
    return bool(
        rec.node_errors
        or m.get("error")
        or m.get("grounded") is False
        or m.get("partial")
        or m.get("stop_reason") in cfg["failure_stop_reasons"]
    )


def _tags(rec: TurnRecording) -> list[str]:
    m = rec.metadata
    tags = [f"stop:{m.get('stop_reason')}", f"grounded:{str(m.get('grounded')).lower()}",
            f"partial:{str(bool(m.get('partial'))).lower()}", f"laps:{m.get('iterations')}",
            f"confidence:{m.get('confidence')}"]
    tags += [f"tool:{t}" for t in m.get("tools_selected", [])]
    tags += [f"domain:{d}" for d in m.get("domains", []) or []]
    if rec.node_errors:
        tags.append("node_error")
    if m.get("failure"):
        tags.append("failure")
    return tags


def _finish(rec: TurnRecording) -> None:
    rec.metadata["tokens"] = rec.tokens()
    rec.metadata["node_errors"] = rec.node_errors
    rec.metadata["failure"] = is_failure(rec)

    # The structured summary goes to the log whatever happens to the trace.
    summary_log.info(json.dumps(scrub(_jsonable({
        "turn_id": rec.turn_id, "session_id": rec.session_id, **{
            k: rec.metadata.get(k) for k in (
                "stop_reason", "iterations", "partial", "confidence", "grounded", "tools_selected",
                "domains", "llm_calls", "latency_ms", "tokens", "failure", "node_errors",
                "prompt_versions")},
        "config_hash": rec.metadata.get("config", {}).get("app_config_hash"),
    })), default=str))

    rate = get_config()["observability"]["success_sample_rate"]
    if not rec.metadata["failure"] and random.random() >= rate:
        return
    otel = _otel()
    if otel is not None:
        _export(rec, otel)
