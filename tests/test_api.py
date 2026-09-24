"""API surface: the streaming protocol, CORS, the turn ceiling, and
recording a turn before it finishes.

The agent turn is replaced where the HTTP layer is what is under test;
the loop itself is tested in test_graph_loop.py. The turn ceiling and
recording are tested against run_turn itself, with a slow fake graph.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from ops_copilot import main
from ops_copilot.agent import turn as turn_mod
from ops_copilot.agent.state import Citation, Evidence, GroundednessResult, new_state
from ops_copilot.api import routes_chat
from ops_copilot.api.progress import to_progress


class _NoMCP:
    connected = False

    async def connect(self):
        raise ConnectionError("not in tests")

    async def close(self):
        pass


async def _fake_turn(question, session_id, *, turn_id="t", emit=None, **_):
    state = new_state(question, session_id, turn_id)
    if emit:
        emit("routed", {"domains": ["diagnostic"], "query_type": "lookup",
                        "entities": {"vehicle_id": "VIN-1042"}, "notes": []})
        emit("plan", {"iteration": 1, "reasoning": "",
                      "tools": [{"tool": "structured_query_tool",
                                 "args": {"sql": "SELECT code FROM error_codes WHERE code = 'ERR_401'"}}]})
        for word in ("ERR_401 ", "is a ", "BMS timeout [e1]."):
            emit("token", {"text": word})
    state.update(answer="ERR_401 is a BMS timeout [e1].", iteration=1, stop_reason="complete",
                 citations=[Citation(claim="ERR_401 is a BMS timeout .", evidence_id="e1")],
                 confidence="medium",
                 evidence=[Evidence(id="e1", tool="structured_query_tool", summary="1 row")],
                 groundedness=GroundednessResult(passed=True))
    return state


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "get_client", lambda: _NoMCP())
    monkeypatch.setattr(routes_chat, "run_turn", _fake_turn)
    with TestClient(main.app) as c:
        yield c


def _events(body: str) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event:
            out.append((event, json.loads(line.split(":", 1)[1].strip())))
    return out


class TestChatStream:
    def test_progress_then_tokens_then_done(self, client):
        with client.stream("POST", "/chat", json={"question": "What is ERR_401?"}) as r:
            assert r.headers["x-accel-buffering"] == "no"
            events = _events(r.read().decode())
        names = [e for e, _ in events]
        assert names[0] == "progress"
        assert names.index("progress") < names.index("token") < names.index("done")
        assert names[-1] == "done"
        messages = [d["message"] for e, d in events if e == "progress"]
        assert "Understood: a diagnostic question about VIN-1042" in messages
        assert "Looking up error code ERR_401" in messages
        assert "".join(d["text"] for e, d in events if e == "token") == "ERR_401 is a BMS timeout [e1]."
        done = dict(events)["done"]
        assert done["turn_id"] == events[0][1]["turn_id"]
        assert done["citations"][0]["evidence_id"] == "e1"

    def test_non_streaming_returns_one_json_reply(self, client):
        r = client.post("/chat", json={"question": "What is ERR_401?", "stream": False})
        assert r.status_code == 200
        assert r.json()["answer"].startswith("ERR_401")
        assert r.json()["grounded"] is True

    def test_empty_question_rejected(self, client):
        assert client.post("/chat", json={"question": ""}).status_code == 422


class TestCors:
    def test_listed_origin_is_allowed(self, client):
        r = client.options("/chat", headers={"Origin": "http://localhost:5173",
                                             "Access-Control-Request-Method": "POST"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_unlisted_origin_is_refused(self, client):
        r = client.options("/chat", headers={"Origin": "https://evil.example",
                                             "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in r.headers
        assert r.headers.get("access-control-allow-origin") != "*"


class TestOtherEndpoints:
    def test_eval_is_honest_that_it_is_not_built(self, client):
        r = client.post("/eval", json={})
        assert r.status_code == 501
        assert "evaluation phase" in r.json()["detail"]

    def test_feedback_rating_validated(self, client):
        assert client.post("/feedback", json={"turn_id": "x", "rating": "meh"}).status_code == 422

    def test_ingest_rejects_unsupported_type(self, client):
        r = client.post("/ingest", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
        assert r.status_code == 415

    def test_health_reports_dependencies_separately(self, client):
        body = client.get("/health").json()
        assert set(body) >= {"status", "database", "mcp", "llm_configured"}


class TestProgressMessages:
    def test_comparison_query_described_in_plain_words(self):
        sql = ("SELECT ... FROM vehicle_telemetry t JOIN vehicle_baseline_specs b ON ... "
               "WHERE t.metric_name IN ('current_draw', 'payload')")
        (msg,) = to_progress("plan", {"iteration": 1, "tools": [
            {"tool": "structured_query_tool", "args": {"sql": sql}}]})
        assert msg["message"] == "Checking current_draw and payload against normal values"
        assert msg["lap"] == 1

    def test_empty_retrieval_reads_as_such(self):
        (msg,) = to_progress("evidence", {"iteration": 2, "items": [
            {"id": "e3", "status": "below_threshold", "summary": "..."}]})
        assert msg["message"] == "No documentation matched closely enough"

    def test_reflect_decisions(self):
        assert to_progress("reflect", {"stop_reason": None, "next_question": "cell health?"})[0][
            "message"] == "Need more: cell health?"
        assert "partial" in to_progress("reflect", {"stop_reason": "exhausted"})[0]["message"]

    def test_internal_events_are_not_leaked(self):
        assert to_progress("status", {"node": "execute"}) == []


class _SlowGraph:
    """Yields one state snapshot, then hangs past any ceiling."""

    def __init__(self, first):
        self.first = first

    async def astream(self, initial, config, stream_mode):
        yield {**initial, **self.first}
        await asyncio.sleep(3600)


class TestTurnCeilingAndRecording:
    @pytest.fixture
    def recorded(self, monkeypatch):
        calls: list[tuple[str, dict]] = []

        async def start(turn_id, session_id, question):
            calls.append(("start", {"turn_id": turn_id}))

        async def end(state):
            calls.append(("end", {"answer": state.get("answer")}))

        monkeypatch.setattr(turn_mod, "_record_start", start)
        monkeypatch.setattr(turn_mod, "_record_end", end)
        return calls

    async def test_ceiling_answers_with_what_was_found(self, monkeypatch, recorded):
        found = [Evidence(id="e1", tool="structured_query_tool",
                          summary="current_draw: 32.6 A vs baseline 23.9 (+36.4%, above_normal)")]
        monkeypatch.setattr(turn_mod, "get_graph", lambda: _SlowGraph({"evidence": found, "iteration": 1}))
        monkeypatch.setattr(turn_mod, "get_config", lambda: {"api": {"turn_timeout_seconds": 0.2}})
        events: list[str] = []
        state = await turn_mod.run_turn("why?", "s", turn_id="t1", emit=lambda e, d: events.append(e))
        assert state["stop_reason"] == "timeout" and state["partial"] is True
        answer = state.get("answer") or ""
        assert "time limit" in answer and "[e1]" in answer
        assert state["confidence"] == "low"
        assert events[:1] == ["answer_reset"]          # client discards any half-answer

    async def test_turn_is_recorded_before_it_runs(self, monkeypatch, recorded):
        monkeypatch.setattr(turn_mod, "get_graph", lambda: _SlowGraph({}))
        monkeypatch.setattr(turn_mod, "get_config", lambda: {"api": {"turn_timeout_seconds": 0.1}})
        await turn_mod.run_turn("why?", "s", turn_id="t2")
        assert [c[0] for c in recorded] == ["start", "end"]
        assert recorded[0][1]["turn_id"] == "t2"


class TestLapStory:
    def test_done_payload_tells_each_lap_and_where_each_fact_came_from(self):
        state = new_state("q", "s", "t")
        state.update(
            iteration=2, answer="x", stop_reason="complete",
            lap_log=[
                {"lap": 1, "kind": "plan", "reasoning": "need readings",
                 "tools": [{"tool": "structured_query_tool", "args": {"sql": "SELECT 1"}}]},
                {"lap": 1, "kind": "reflect", "decision": "continue", "missing": ["alternative"],
                 "next_question": "cell health?"},
                {"lap": 2, "kind": "plan", "reasoning": "check battery", "tools": []},
                {"lap": 2, "kind": "reflect", "decision": "complete", "missing": [],
                 "next_question": None},
            ],
            evidence=[Evidence(id="e1", tool="structured_query_tool", summary="a", iteration=1,
                               tool_args={"sql": "SELECT 1"}),
                      Evidence(id="e2", tool="rag_retrieval_tool", summary="b", iteration=2,
                               tool_args={"query": "overload"}, source_doc="SB-114",
                               rerank_score=0.91)],
        )
        body = routes_chat.to_response(state).model_dump()
        assert [(lap["lap"], lap["decision"], lap["found"]) for lap in body["laps"]] == [
            (1, "continue", ["e1"]), (2, "complete", ["e2"])]
        assert body["laps"][0]["next_question"] == "cell health?"
        e1, e2 = body["evidence"]
        assert e1["sql"] == "SELECT 1" and e1["lap"] == 1
        assert (e2["query"], e2["source_doc"], e2["score"]) == ("overload", "SB-114", 0.91)
