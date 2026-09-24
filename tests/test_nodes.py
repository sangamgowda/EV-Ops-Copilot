"""The code-only parts of the nodes: arithmetic, grounding tiers,
stream decoding, loop decisions. No LLM, no network."""

from __future__ import annotations

from ops_copilot.agent.context import call_key
from ops_copilot.agent.nodes.groundedness import (
    extract_numbers,
    unresolved_citations,
    unsupported_causal_claims,
    untraceable_numbers,
)
from ops_copilot.agent.nodes.observe import compare
from ops_copilot.agent.nodes.plan import dedupe
from ops_copilot.agent.nodes.reflect import decide
from ops_copilot.agent.nodes.synthesize import AnswerStreamer
from ops_copilot.agent.state import (
    Citation,
    Evidence,
    EvidenceStatus,
    ReflectOutput,
    ToolCall,
    Verdict,
)


class TestCompare:
    def test_above(self):
        assert compare(15.0, 11.0, 10) == (36.4, Verdict.ABOVE_NORMAL)

    def test_within_tolerance(self):
        assert compare(10.5, 10.0, 10) == (5.0, Verdict.NORMAL)

    def test_below(self):
        assert compare(126.7, 169.4, 10) == (-25.2, Verdict.BELOW_NORMAL)

    def test_no_baseline(self):
        assert compare(5.0, None, 10) == (None, Verdict.NO_BASELINE)


def ev(i: int, summary: str, **kw) -> Evidence:
    return Evidence(id=f"e{i}", tool=kw.pop("tool", "structured_query_tool"), summary=summary, **kw)


class TestGrounding:
    def test_unresolved(self):
        cites = [Citation(claim="x", evidence_id="e7")]
        assert unresolved_citations("a [e1] b [e3]", cites, {"e1", "e2"}) == ["e3", "e7"]

    def test_identifiers_are_not_numbers(self):
        nums = [v for _, v in extract_numbers("VIN-1042 logged ERR_401 per SB-114 on 2026-09-14, "
                                                "firmware 2.6.0, in Q2 [e3], drew 33.2 A")]
        assert nums == [33.2]

    def test_rounded_evidence_number_traces(self):
        evidence = [ev(1, "current_draw 33.2 A vs 25 (+32.8%)", delta_pct=32.8)]
        assert untraceable_numbers("Current draw is 33% above baseline.", evidence) == []

    def test_invented_number_flagged(self):
        evidence = [ev(1, "current_draw 33.2 A vs 25")]
        assert untraceable_numbers("Range fell 41% last week.", evidence) == ["41"]

    def test_small_counts_ignored(self):
        assert untraceable_numbers("Checked 3 vehicles over 2 laps.", []) == []

    def test_causal_claim_needs_supporting_evidence(self):
        empty = ev(1, "No documents matched.", tool="rag_retrieval_tool", status=EvidenceStatus.EMPTY)
        flagged = unsupported_causal_claims("Range dropped because of overload [e1].", [], [empty])
        assert flagged == ["Range dropped because of overload [e1]."]

    def test_causal_claim_with_document_passes(self):
        doc = ev(1, "Overload raises current.", tool="rag_retrieval_tool", source_doc="SB-114")
        assert unsupported_causal_claims("Current is high because of overload [e1].", [], [doc]) == []

    def test_stating_a_gap_is_not_causal(self):
        text = "The cause cannot be established because no documentation covers this model."
        assert unsupported_causal_claims(text, [], []) == []


class TestAnswerStreamer:
    def test_decodes_across_arbitrary_chunk_boundaries(self):
        payload = '{"answer": "Line one\\nsays \\"hi\\" \\u00e9 done", "citations": []}'
        for size in (1, 2, 3, 5, 11):
            s = AnswerStreamer()
            out = "".join(s.feed(payload[i:i + size]) for i in range(0, len(payload), size))
            assert out == 'Line one\nsays "hi" é done', size


class TestReflectDecision:
    def test_complete(self):
        assert decide(ReflectOutput(sufficient=True), 1, 3)["stop_reason"] == "complete"

    def test_exhausted_is_partial(self):
        d = decide(ReflectOutput(sufficient=True, partial=True), 2, 3)
        assert (d["stop_reason"], d["partial"]) == ("exhausted", True)

    def test_insufficient_continues_with_question(self):
        d = decide(ReflectOutput(sufficient=False, next_question="baseline?"), 1, 3)
        assert d["stop_reason"] is None and d["next_question"] == "baseline?"

    def test_insufficient_without_direction_is_exhausted(self):
        assert decide(ReflectOutput(sufficient=False), 1, 3)["stop_reason"] == "exhausted"

    def test_cap(self):
        d = decide(ReflectOutput(sufficient=False, next_question="more?"), 3, 3)
        assert (d["stop_reason"], d["partial"]) == ("cap_reached", True)

    def test_model_cannot_claim_complete_while_partial(self):
        # stop_reason comes from the booleans, not the model's own field.
        d = decide(ReflectOutput(sufficient=True, partial=True, stop_reason="complete"), 1, 3)
        assert d["stop_reason"] == "exhausted"


class TestDedupe:
    def test_whitespace_and_case_do_not_make_a_query_new(self):
        a = ToolCall(tool="structured_query_tool", args={"sql": "SELECT 1"})
        b = ToolCall(tool="structured_query_tool", args={"sql": "select   1"})
        assert dedupe([b], [call_key(a)], ["diagnostic"], 4) == []

    def test_invalid_rag_domain_is_repaired(self):
        c = ToolCall(tool="rag_retrieval_tool", args={"query": "q", "domain": "sales"})
        (out,) = dedupe([c], [], ["business"], 4)
        assert out.args["domain"] == "business"


class TestPromptExamples:
    def test_every_sql_example_in_the_plan_prompt_passes_validation(self):
        """The prompt teaches by example. An example the validator
        rejects teaches the model to write rejected queries — the
        prompt and the code have drifted apart."""
        import json
        import re

        from ops_copilot.settings import get_config, get_schema_config, load_prompt
        from ops_copilot.sql.validator import SQLValidator

        body, _ = load_prompt("plan")
        validator = SQLValidator(get_schema_config(), get_config())
        sqls = [json.loads(f'"{m}"') for m in re.findall(r'"sql": "((?:[^"\\]|\\.)*)"', body)
                if m != "..."]
        assert len(sqls) >= 3
        for sql in sqls:
            result = validator.validate(sql)
            assert result.ok, (sql, result.reasons)


class TestJsonModeMessages:
    def test_json_word_is_guaranteed_in_json_mode(self):
        from ops_copilot.llm.client import _messages

        msgs = _messages("Return ONLY: {\"sufficient\": true}", "question", json_mode=True)
        assert "json" in (msgs[0]["content"] + msgs[1]["content"]).lower()

    def test_prompt_untouched_when_it_already_says_json(self):
        from ops_copilot.llm.client import _messages

        assert _messages("Return a JSON object.", "q", json_mode=True)[0]["content"] == "Return a JSON object."
        assert _messages("Plain prose.", "q", json_mode=False)[0]["content"] == "Plain prose."


class TestDocumentCoverage:
    def test_uncovered_model_is_stated_in_the_evidence(self):
        from ops_copilot.agent.nodes.observe import _coverage

        chunk = {"applies_to_models": ["SC-F50", "SC-F45"], "boosted": False}
        note = _coverage(chunk, {"entity_id": "VIN-1023"})
        assert "SC-F50, SC-F45" in note and "does NOT list" in note

    def test_covered_model_is_not_flagged(self):
        from ops_copilot.agent.nodes.observe import _coverage

        chunk = {"applies_to_models": ["SC-F50"], "boosted": True}
        assert "does NOT" not in _coverage(chunk, {"entity_id": "VIN-1042"})
        # No vehicle asked about: nothing to flag.
        assert "does NOT" not in _coverage({"applies_to_models": [], "boosted": False}, {})
