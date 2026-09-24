"""Loop behaviour. These are the tests that prove it is an agent.

The real graph, the real nodes, the real routing functions. Only the
edges of the system are replaced: the LLM with a scripted fake that
answers per node, and the MCP client with a fake that answers per
tool. Everything between — dedupe, evidence ids, computed verdicts,
stop reasons, the backward edge — runs for real.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest

from ops_copilot.agent import raw_store
from ops_copilot.agent.graph import build_graph
from ops_copilot.agent.nodes import groundedness, plan, reflect, router, synthesize
from ops_copilot.agent.state import (
    EvidenceStatus,
    PlanOutput,
    ReflectOutput,
    RouterOutput,
    ToolCall,
    new_state,
)


def sql(n: int) -> ToolCall:
    # Distinct, validator-clean SQL per call so dedupe treats them as new.
    return ToolCall(tool="structured_query_tool",
                    args={"sql": f"SELECT code, meaning FROM error_codes WHERE severity = 'w{n}' LIMIT 5"})


def rag(q: str) -> ToolCall:
    return ToolCall(tool="rag_retrieval_tool", args={"query": q, "domain": "diagnostic"})


class Script:
    """Scripted LLM: a queue of outputs per node, and a call log."""

    def __init__(self, **queues: list[Any]) -> None:
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: dict[str, int] = defaultdict(int)

    def next(self, node: str) -> Any:
        self.calls[node] += 1
        q = self.queues[node]
        return q.pop(0) if len(q) > 1 else q[0]


class FakeMCP:
    def __init__(self, handler: Callable[[str, dict], dict]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict, turn_id: str) -> dict:
        if name == "resolve_entity_tool":
            # The router's up-front VIN check; every test vehicle exists.
            return {"turn_id": turn_id, "tool": name,
                    "result": {"status": "exact", "value": args["vehicle_id"],
                               "suggestions": [], "note": None}}
        self.calls.append((name, args))
        return {"turn_id": turn_id, "tool": name, "result": self.handler(name, args)}


def answer(text: str, cites: list[str] | None = None, confidence: str | None = None) -> str:
    """Synthesize writes plain prose now; citations and confidence are
    derived in code from the inline tags, so only the text matters."""
    return text


@pytest.fixture
def wire(monkeypatch):
    """Install a script and a fake MCP; return a runner."""
    async def no_raw(turn_id, payload):
        return None

    monkeypatch.setattr(raw_store, "put", no_raw)

    def install(script: Script, mcp: FakeMCP):
        async def cj(node, system, user, schema):
            return script.next(node)

        async def fake_stream(node, system, user, result, *, json_mode=False):
            text = script.next(node)
            for i in range(0, len(text), 7):
                yield text[i:i + 7]
            result.text = text

        for mod in (router, plan, reflect, groundedness):
            monkeypatch.setattr(mod, "complete_json", cj)
        monkeypatch.setattr(synthesize, "stream", fake_stream)

        async def run(question: str = "q") -> dict:
            events: list[tuple[str, dict]] = []
            state = await build_graph().ainvoke(
                new_state(question, "s1", "t1"),
                config={"configurable": {"mcp": mcp, "emit": lambda e, d: events.append((e, d))},
                        "recursion_limit": 50},
            )
            state["_events"] = events
            return state
        return run
    return install


def routed(hint: str) -> RouterOutput:
    return RouterOutput(domains=["diagnostic"], query_type=hint, complexity_hint=hint,
                        entities={"vehicle_id": "V-007"})


ROWS = {"status": "ok", "rows": [{"code": "ERR_601", "meaning": "Sustained over-current"}]}
COMPARISON = {"status": "ok", "rows": [{"metric": "current_draw", "actual": 33.2, "baseline": 25.0,
                                        "unit": "A", "tolerance_pct": 10}]}
DOC = {"status": "ok", "domain": "diagnostic", "query": "overload", "best_score": 0.91,
       "chunks": [{"chunk_id": 7, "doc_id": "SB-114_sustained_overload",
                   "title": "SB-114 — Sustained overload", "section_path": "Cause",
                   "content": "SB-114\n\nOverload raises drive current.", "rerank_score": 0.91}]}


class TestLoopControl:
    async def test_lookup_exits_after_one_lap(self, wire):
        """Simple questions must not burn laps they do not need."""
        s = Script(router=[routed("lookup")], plan=[PlanOutput(reasoning="", tool_calls=[sql(1)])],
                   synthesize=[answer("ERR_601 means sustained over-current [e1].", ["e1"])])
        state = await wire(s, FakeMCP(lambda n, a: ROWS))()
        assert state["iteration"] == 1
        assert state["stop_reason"] == "complete"
        assert s.calls["reflect"] == 0          # skipped: nothing to reflect on
        assert state["groundedness"].passed

    async def test_causal_question_loops(self, wire):
        """A 'why' needs measurement + baseline + mechanism, so one
        lap cannot be sufficient."""
        s = Script(
            router=[routed("explain")],
            plan=[PlanOutput(reasoning="", tool_calls=[sql(1)]),
                  PlanOutput(reasoning="", tool_calls=[rag("overload current draw")])],
            reflect=[ReflectOutput(sufficient=False, missing=["mechanism"],
                                   next_question="what documents explain high current draw?"),
                     ReflectOutput(sufficient=True)],
            synthesize=[answer("Current draw is 32.8% above baseline [e1] because sustained "
                               "overload raises drive current [e2].", ["e1", "e2"])],
        )
        mcp = FakeMCP(lambda n, a: COMPARISON if n == "structured_query_tool" else DOC)
        state = await wire(s, mcp)()
        assert state["iteration"] == 2
        assert state["stop_reason"] == "complete"
        assert [e.tool for e in state["evidence"]] == ["structured_query_tool", "rag_retrieval_tool"]
        assert state["groundedness"].passed

    async def test_stops_at_iteration_cap(self, wire):
        s = Script(
            router=[routed("explain")],
            plan=[PlanOutput(reasoning="", tool_calls=[sql(i)]) for i in range(1, 4)],
            reflect=[ReflectOutput(sufficient=False, missing=["mechanism"], next_question="more?")],
            synthesize=[answer("Partial [e1].", ["e1"], "low")],
        )
        state = await wire(s, FakeMCP(lambda n, a: ROWS))()
        assert state["iteration"] == 3
        assert state["stop_reason"] == "cap_reached"
        assert state["partial"] is True

    async def test_stops_when_no_new_evidence(self, wire):
        """A lap that added nothing will not do better next time."""
        s = Script(
            router=[routed("explain")],
            plan=[PlanOutput(reasoning="", tool_calls=[sql(1)])],   # lap 2 repeats lap 1
            reflect=[ReflectOutput(sufficient=False, missing=["mechanism"], next_question="more?")],
            synthesize=[answer("Only a partial answer [e1].", ["e1"], "low")],
        )
        mcp = FakeMCP(lambda n, a: ROWS)
        state = await wire(s, mcp)()
        assert state["stop_reason"] == "no_new_evidence"
        assert state["iteration"] == 2
        assert len(mcp.calls) == 1              # the repeat was never sent

    async def test_exhausted_exits_with_partial(self, wire):
        """Missing-but-unreachable is a stop condition, not a reason
        to keep looping."""
        s = Script(
            router=[routed("explain")],
            plan=[PlanOutput(reasoning="", tool_calls=[sql(1), rag("why is current high on Volt 1 Ultra")])],
            reflect=[ReflectOutput(sufficient=True, partial=True, missing=["mechanism"],
                                   stop_reason="exhausted")],
            synthesize=[answer("ERR_601 was logged [e1]. The cause cannot be established because "
                               "no documentation covers this model [e2].", ["e1", "e2"], "low")],
        )
        mcp = FakeMCP(lambda n, a: ROWS if n == "structured_query_tool"
                      else {"status": "empty", "domain": "diagnostic", "chunks": []})
        state = await wire(s, mcp)()
        assert state["iteration"] == 1
        assert state["stop_reason"] == "exhausted"
        assert state["partial"] is True
        assert "mechanism" in state["gaps"]
        # The "because" states the gap; it must not be flagged as causal.
        assert state["groundedness"].passed

    async def test_never_loops_back_to_router(self, wire):
        """Grounding failures retry synthesis only. Re-classifying the
        domain fixes nothing."""
        bad = answer("Current draw is 33.2 A [e9].", ["e9"])   # e9 does not exist
        s = Script(router=[routed("lookup")], plan=[PlanOutput(reasoning="", tool_calls=[sql(1)])],
                   synthesize=[bad])
        state = await wire(s, FakeMCP(lambda n, a: ROWS))()
        assert s.calls["router"] == 1
        assert s.calls["plan"] == 1
        assert s.calls["synthesize"] == 2      # one retry, then out
        assert not state["groundedness"].passed
        assert state["confidence"] == "low"
        assert "could not be verified" in state["answer"]
        assert any(e == "answer_reset" for e, _ in state["_events"])


class TestEvidenceAccumulation:
    async def test_evidence_persists_across_laps(self, wire):
        """Without accumulation, lap 2 repeats lap 1."""
        s = Script(
            router=[routed("explain")],
            plan=[PlanOutput(reasoning="", tool_calls=[sql(1)]),
                  PlanOutput(reasoning="", tool_calls=[sql(2)])],
            reflect=[ReflectOutput(sufficient=False, missing=["baseline"], next_question="baseline?"),
                     ReflectOutput(sufficient=True)],
            synthesize=[answer("Done [e1] [e2].", ["e1", "e2"])],
        )
        state = await wire(s, FakeMCP(lambda n, a: ROWS))()
        assert [(e.id, e.iteration) for e in state["evidence"]] == [("e1", 1), ("e2", 2)]
        assert len(state["tool_history"]) == 2

    async def test_failed_tool_becomes_evidence(self, wire):
        s = Script(router=[routed("lookup")],
                   plan=[PlanOutput(reasoning="", tool_calls=[sql(1), rag("overload")])],
                   reflect=[ReflectOutput(sufficient=True, partial=True, stop_reason="exhausted")],
                   synthesize=[answer("ERR_601 is sustained over-current [e1].", ["e1"])])

        def handler(name, args):
            if name == "rag_retrieval_tool":
                raise ConnectionError("vector store down")
            return ROWS
        state = await wire(s, FakeMCP(handler))()
        statuses = {e.tool: e.status for e in state["evidence"]}
        # One tool failing did not take the other down with it.
        assert statuses["structured_query_tool"] == EvidenceStatus.OK
        assert statuses["rag_retrieval_tool"] == EvidenceStatus.FAILED

    async def test_empty_retrieval_becomes_evidence(self, wire):
        s = Script(router=[routed("explain")],
                   plan=[PlanOutput(reasoning="", tool_calls=[rag("wiper motor")])],
                   reflect=[ReflectOutput(sufficient=True, partial=True, stop_reason="exhausted")],
                   synthesize=[answer("No documentation covers this [e1].", ["e1"], "low")])
        state = await wire(s, FakeMCP(lambda n, a: {"status": "below_threshold", "best_score": 0.3,
                                                    "chunks": [], "domain": "diagnostic"}))()
        (ev,) = state["evidence"]
        assert ev.status == EvidenceStatus.BELOW_THRESHOLD
        assert not ev.supports_causal_claim()

    async def test_rejected_sql_becomes_failed_evidence_without_a_round_trip(self, wire):
        bad = ToolCall(tool="structured_query_tool", args={"sql": "DROP TABLE vehicles"})
        s = Script(router=[routed("lookup")], plan=[PlanOutput(reasoning="", tool_calls=[bad])],
                   reflect=[ReflectOutput(sufficient=True, partial=True, stop_reason="exhausted")],
                   synthesize=[answer("The query could not be run [e1].", ["e1"], "low")])
        mcp = FakeMCP(lambda n, a: ROWS)
        state = await wire(s, mcp)()
        assert mcp.calls == []
        assert state["evidence"][0].status == EvidenceStatus.FAILED
        assert "SELECT" in state["evidence"][0].summary


class TestNodeFailures:
    """The guide's rule: one node failing must not fail the turn."""

    async def test_failed_reflect_answers_with_what_exists(self, wire, monkeypatch):
        s = Script(router=[routed("explain")],
                   plan=[PlanOutput(reasoning="", tool_calls=[sql(1)])],
                   reflect=[RuntimeError("model unavailable")],
                   synthesize=[answer("ERR_601 is sustained over-current [e1].", ["e1"], "low")])

        async def cj(node, system, user, schema):
            out = s.next(node)
            if isinstance(out, Exception):
                raise out
            return out
        run = wire(s, FakeMCP(lambda n, a: ROWS))
        monkeypatch.setattr(reflect, "complete_json", cj)
        state = await run()
        assert state["iteration"] == 1                     # stopped, did not spin
        assert state["stop_reason"] == "exhausted" and state["partial"] is True
        assert state["answer"]
        assert any(e == "node_error" for e, _ in state["_events"])

    async def test_failed_synthesis_still_returns_the_evidence(self, wire, monkeypatch):
        s = Script(router=[routed("lookup")], plan=[PlanOutput(reasoning="", tool_calls=[sql(1)])],
                   synthesize=["unused"])
        run = wire(s, FakeMCP(lambda n, a: ROWS))

        async def broken_stream(*a, **k):
            raise ConnectionError("provider down")
            yield  # pragma: no cover - makes this an async generator
        monkeypatch.setattr(synthesize, "stream", broken_stream)
        state = await run()
        assert "could not write a full answer" in state["answer"]
        assert "[e1]" in state["answer"]
        assert state["confidence"] == "low"

    async def test_failed_router_searches_both_domains(self, wire, monkeypatch):
        s = Script(plan=[PlanOutput(reasoning="", tool_calls=[sql(1)])],
                   reflect=[ReflectOutput(sufficient=True)],
                   synthesize=[answer("ERR_601 is sustained over-current [e1].", ["e1"])])
        run = wire(s, FakeMCP(lambda n, a: ROWS))

        async def boom(*a, **k):
            raise ValueError("not JSON")
        monkeypatch.setattr(router, "complete_json", boom)
        state = await run()
        assert state["domains"] == ["diagnostic", "business"]
        assert state["answer"]


class TestTracing:
    async def test_real_loop_is_recorded_lap_by_lap(self, wire, monkeypatch):
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from ops_copilot.observability import tracing

        exporter = InMemorySpanExporter()
        otel = tracing.make_otel(exporter, batch=False)
        monkeypatch.setattr(tracing, "_otel", lambda: otel)
        s = Script(
            router=[routed("explain")],
            plan=[PlanOutput(reasoning="measure first", tool_calls=[sql(1)]),
                  PlanOutput(reasoning="then the documents", tool_calls=[rag("overload")])],
            reflect=[ReflectOutput(sufficient=False, missing=["mechanism"], next_question="why?"),
                     ReflectOutput(sufficient=True)],
            synthesize=[answer("Current draw is 32.8% above baseline [e1] due to overload [e2].")],
        )
        run = wire(s, FakeMCP(lambda n, a: COMPARISON if n == "structured_query_tool" else DOC))
        with tracing.turn_trace("0" * 31 + "1", "s1", "q"):
            await run()

        spans = list(exporter.get_finished_spans())
        by_id = {sp.context.span_id: sp for sp in spans}

        def parent(sp):
            return by_id[sp.parent.span_id].name if sp.parent else None

        laps = [sp for sp in spans if sp.name.startswith("lap ")]
        assert sorted(sp.name for sp in laps) == ["lap 1", "lap 2"]
        for name in ("plan", "execute", "observe", "reflect"):
            assert sorted(parent(sp) for sp in spans if sp.name == name) == ["lap 1", "lap 2"], name
        assert {sp.name for sp in spans if parent(sp) == "turn"} == {
            "router", "lap 1", "lap 2", "synthesize", "grounding"}

        def out(sp):
            return json.loads(sp.attributes["langfuse.observation.output"])

        reflects = sorted((sp for sp in spans if sp.name == "reflect"), key=lambda sp: parent(sp))
        assert out(reflects[0])["open_gaps"] == ["mechanism"]
        assert out(reflects[1])["stop_reason"] == "complete"
        observed = sorted((sp for sp in spans if sp.name == "observe"), key=lambda sp: parent(sp))
        assert out(observed[0])["evidence"][0]["verdict"] == "above_normal"
        assert out(observed[1])["evidence"][0]["rerank_score"] == 0.91
        grounding = next(sp for sp in spans if sp.name == "grounding")
        assert out(grounding)["groundedness"]["passed"] is True     # recorded, not empty
