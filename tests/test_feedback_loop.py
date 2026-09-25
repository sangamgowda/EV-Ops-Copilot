"""The feedback loop's decisions, as pure functions: which cluster a
failure lands in, what counts as a rephrase, that nothing is promoted
without a person's answer, and when a cluster counts as resolved."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ops_copilot.feedback.capture import is_rephrase
from ops_copilot.feedback.promote import (
    build_case,
    cluster_status,
    coverage_warnings,
    queue,
)
from ops_copilot.feedback.signature import signature

HEALTHY = {"domains": ["diagnostic"], "query_type": "explain", "grounded": True, "stop_reason": "complete",
           "tools_selected": ["rag_retrieval_tool", "structured_query_tool"], "node_errors": [],
           "evidence_statuses": ["structured_query_tool:ok", "rag_retrieval_tool:ok"], "latency_ms": 9000}


def sig(**changes):
    return signature({**HEALTHY, **changes}, {"vehicle_id": "V-042"}, slow_ms=20000)


class TestSignature:
    def test_healthy_trace_needs_a_human(self):
        assert sig().cluster == "no_signal"

    @pytest.mark.parametrize(("changes", "cluster"), [
        ({"node_errors": ["router: RateLimitError: 429"]}, "wrong_domain"),
        ({"domains": ["business"]}, "wrong_domain"),               # a vehicle question routed as sales
        ({"tools_selected": [], "evidence_statuses": []}, "wrong_tool"),
        ({"query_type": "lookup", "tools_selected": ["rag_retrieval_tool"]}, "wrong_tool"),
        ({"node_errors": ["plan: TimeoutError: x"]}, "step_failed"),
        ({"evidence_statuses": ["structured_query_tool:failed"]}, "sql_failed"),
        ({"evidence_statuses": ["structured_query_tool:ok", "rag_retrieval_tool:below_threshold"]},
         "empty_retrieval"),
        ({"grounded": False}, "grounding_failure"),
        ({"stop_reason": "cap_reached"}, "hit_limit"),
        ({"stop_reason": "timeout"}, "hit_limit"),
        ({"latency_ms": 31000}, "slow"),
    ])
    def test_each_cluster(self, changes, cluster):
        assert sig(**changes).cluster == cluster

    def test_first_match_names_the_cluster_all_are_kept(self):
        s = sig(grounded=False, stop_reason="cap_reached",
                evidence_statuses=["structured_query_tool:ok", "rag_retrieval_tool:empty"])
        assert s.cluster == "empty_retrieval"
        assert s.signals == ["empty_retrieval", "grounding_failure", "hit_limit"]

    def test_slow_only_when_nothing_else_is_wrong(self):
        assert sig(latency_ms=40000, grounded=False).cluster == "grounding_failure"

    def test_missing_outcome_is_no_signal(self):
        assert signature(None).cluster == "no_signal"


CFG = {"implicit_negative_window_seconds": 30, "implicit_negative_min_similarity": 55}


class TestRephrase:
    def test_reworded_question_soon_after(self):
        assert is_rephrase("Why did range drop on V-042?", "why is V-042 range dropping so much", 12, CFG)

    def test_asking_again_verbatim_counts(self):
        assert is_rephrase("What does ERR_205 mean?", "what does ERR_205 mean?", 5, CFG)

    def test_a_follow_up_is_not_a_rephrase(self):
        assert not is_rephrase("Why did range drop on V-042?", "How many Ultras sold in the south?", 10, CFG)

    def test_outside_the_window(self):
        assert not is_rephrase("Why did range drop on V-042?", "Why did range drop on V-042?", 45, CFG)


def item(cluster="empty_retrieval", turn="abcdef1234567890", **kw):
    return {"turn_id": turn, "cluster": cluster, "signals": [cluster], "rating": "down",
            "comment": None, "question": "Why is V-064 slow to charge?", "review_status": "new",
            "created_at": datetime(2026, 9, 26, tzinfo=UTC), **kw}


class TestPromotion:
    def test_no_reference_answer_no_promotion(self):
        with pytest.raises(ValueError, match="reference answer"):
            build_case(item(), {"reference_answer": "  "})

    def test_case_is_tagged_with_its_cluster(self):
        case = build_case(item(), {"reference_answer": "Charger at 0.34 kW; see SB-140.",
                                   "must_contain": ["charger"], "vehicle_id": "V-064"})
        assert case["source"] == "promoted" and case["cluster"] == "empty_retrieval"
        assert "cluster:empty_retrieval" in case["tags"]
        assert case["expected"]["must_contain"] == ["charger"]
        assert case["feedback_turn_id"] == "abcdef1234567890"

    def test_warns_before_padding_a_cluster(self):
        existing = [{"id": f"prm_{i}", "cluster": "empty_retrieval", "question": f"q{i} unrelated"}
                    for i in range(3)]
        case = build_case(item(), {"reference_answer": "x"})
        assert any("already has 3" in w for w in coverage_warnings(case, existing))

    def test_warns_on_a_near_duplicate_question(self):
        existing = [{"id": "prm_old", "cluster": "other", "question": "Why is V-064 so slow to charge?"}]
        case = build_case(item(), {"reference_answer": "x"})
        assert any("prm_old" in w for w in coverage_warnings(case, existing))


class TestQueueAndResolution:
    def test_biggest_cluster_first_and_one_item_per_turn(self):
        items = [item("grounding_failure", "t1"), item("empty_retrieval", "t2"),
                 item("empty_retrieval", "t3"), item("empty_retrieval", "t3", rating="implicit_down")]
        groups = queue(items)
        assert [c for c, _ in groups] == ["empty_retrieval", "grounding_failure"]
        assert len(groups[0][1]) == 2                       # t3's two flags are one item

    def test_resolved_needs_passing_cases_and_no_new_flags(self):
        promoted = [{"id": "prm_a", "cluster": "empty_retrieval", "failure": "2026-09-24: down"}]
        old_flag = item(created_at=datetime(2026, 9, 23, tzinfo=UTC), review_status="promoted")
        (row,) = cluster_status([old_flag], promoted, {"prm_a": True})
        assert row["state"] == "resolved"

    def test_recurring_when_flags_keep_coming_after_the_fix(self):
        promoted = [{"id": "prm_a", "cluster": "empty_retrieval", "failure": "2026-09-24: down"}]
        new_flag = item(created_at=datetime(2026, 9, 26, tzinfo=UTC))
        (row,) = cluster_status([new_flag], promoted, {"prm_a": True})
        assert row["state"] == "recurring" and row["new_flags_since_promotion"] == 1

    def test_not_recurring_before_its_cases_have_run(self):
        promoted = [{"id": "prm_a", "cluster": "empty_retrieval", "failure": "2026-09-24: down"}]
        new_flag = item(created_at=datetime(2026, 9, 26, tzinfo=UTC))
        (row,) = cluster_status([new_flag], promoted, {})
        assert row["state"] == "awaiting eval run"

    def test_fix_in_progress_while_a_case_fails(self):
        promoted = [{"id": "prm_a", "cluster": "empty_retrieval", "failure": "2026-09-24: down"}]
        (row,) = cluster_status([], promoted, {"prm_a": False})
        assert row["state"] == "fix in progress"
