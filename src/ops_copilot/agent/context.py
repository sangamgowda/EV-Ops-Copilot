"""What nodes share: how evidence is shown to a model, how a tool call
is fingerprinted, and how progress reaches the client.

Evidence rendering lives in one place on purpose. Plan, Reflect and
Synthesize must all see evidence the same way — same ids, same
status wording — or a citation that means one thing to Plan means
another to Synthesize.

The progress channel is an optional callable passed per request in
LangGraph's `config["configurable"]["emit"]`. The API wires it to the
SSE stream; evaluation leaves it unset. Nodes never know which.

`guarded` is the per-node safety net: a node that raises (an LLM
outage, a malformed reply that survived repair, a bug) returns its
declared fallback instead of failing the whole turn. Each fallback is
chosen so the loop still ends and still answers with what it has.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ops_copilot.agent.state import Evidence, EvidenceStatus, ToolCall
from ops_copilot.observability.tracing import record_node_error
from ops_copilot.settings import get_config

Emit = Callable[[str, dict[str, Any]], Any]
Node = Callable[..., Awaitable[dict[str, Any]]]

log = logging.getLogger(__name__)


def configurable(config: Any) -> dict[str, Any]:
    if not config:
        return {}
    return dict(config.get("configurable", {}) or {})


async def emit(config: Any, event: str, data: dict[str, Any]) -> None:
    fn = configurable(config).get("emit")
    if fn is None:
        return
    out = fn(event, data)
    if hasattr(out, "__await__"):
        await out


def guarded(name: str, fallback: Callable[[Any, Exception], dict[str, Any]]) -> Callable[[Node], Node]:
    """Turn a node's exception into its fallback update, logged and
    reported to the client, instead of a failed turn."""
    def decorator(fn: Node) -> Node:
        @functools.wraps(fn)
        async def wrapper(state: Any, config: Any = None) -> dict[str, Any]:
            try:
                return await fn(state, config)
            except Exception as exc:
                log.exception("%s failed; using fallback", name)
                record_node_error(name, exc)
                await emit(config, "node_error", {"node": name,
                                                  "error": f"{type(exc).__name__}: {exc}"[:300]})
                return fallback(state, exc)
        return wrapper
    return decorator


def call_key(call: ToolCall | dict[str, Any]) -> str:
    """Canonical fingerprint. Whitespace and case in SQL do not make a
    query new; re-running it is exactly what the history exists to stop."""
    if isinstance(call, ToolCall):
        tool, args = call.tool, call.args
    else:
        tool, args = call["tool"], call["args"]
    norm = {k: (" ".join(str(v).split()).lower() if isinstance(v, str) else v)
            for k, v in sorted(args.items()) if v is not None}
    return f"{tool}:{json.dumps(norm, sort_keys=True)}"


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:g}"


def render_evidence(e: Evidence) -> str:
    head = f"[{e.id}] {e.tool} · {e.status.value} · lap {e.iteration}"
    if e.source_doc:
        head += f" · source: {e.source_doc}"
    lines = [head, f"  {e.summary}"]
    if e.verdict is not None and e.actual is not None:
        unit = f" {e.unit}" if e.unit else ""
        delta = f"{e.delta_pct:+.1f}%" if e.delta_pct is not None else "n/a"
        lines.append(
            f"  comparison (computed): {e.metric} actual {_fmt(e.actual)}{unit} vs baseline "
            f"{_fmt(e.baseline)}{unit} -> {delta} ({e.verdict.value})"
        )
    if e.error:
        lines.append(f"  error: {e.error}")
    return "\n".join(lines)


def evidence_block(evidence: list[Evidence]) -> str:
    """All evidence, capped. Non-OK entries are kept preferentially:
    an absence is information the model must not lose."""
    cap = get_config()["agent"]["max_evidence_entries"]
    if len(evidence) > cap:
        misses = [e for e in evidence if e.status != EvidenceStatus.OK]
        hits = [e for e in evidence if e.status == EvidenceStatus.OK]
        keep = {e.id for e in misses[-cap:]}
        keep |= {e.id for e in hits[-(cap - len(keep)):]} if cap > len(keep) else set()
        evidence = [e for e in evidence if e.id in keep]
    if not evidence:
        return "(no evidence gathered yet)"
    return "\n\n".join(render_evidence(e) for e in evidence)
