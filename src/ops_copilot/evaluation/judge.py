"""LLM-as-judge — only where understanding is actually required.

Bias controls, all of them cheap:

  temperature 0
  anchored rubric — what a 3 looks like vs a 4, written down in
    config/prompts/judge.md. Vague rubrics are where bias enters.
  compare against a REFERENCE, not in isolation. The question
    becomes "does this contain what the reference contains", so
    length stops being an advantage. This is the verbosity-bias fix.
  position swap — every pairwise comparison runs in both orders and
    counts only if the verdicts agree.
  a different model family from the system under test, so an answer
    is never graded by the model that wrote it.

The judge is versioned: its prompt hash is stored with every score,
and scores from different judge versions are never compared.

And the part worth being judged on: humans label a sample, and
`agreement` measures how often the judge agrees with them. That
number is reported next to every judge score. If agreement drops, the
judge prompt is what is broken.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from ops_copilot.llm.client import complete_json
from ops_copilot.settings import load_prompt

DIMENSIONS = ("completeness", "correctness", "hedging")


class JudgeScore(BaseModel):
    completeness: int = Field(ge=1, le=4)
    correctness: int = Field(ge=1, le=4)
    hedging: int = Field(ge=1, le=4)
    notes: str = ""


class PairwiseVerdict(BaseModel):
    closer: Literal["A", "B", "tie"]
    reason: str = ""


def judge_version() -> str:
    """Prompt, model and generation settings together: changing any of
    them makes a different judge, whose scores are not comparable."""
    import hashlib

    from ops_copilot.settings import get_config, model_for

    llm = get_config()["llm"]
    parts = [load_prompt("judge")[1], model_for("judge"), str(llm["temperature"].get("judge")),
             str(llm["max_tokens"].get("judge")), str(llm.get("reasoning_effort", {}).get("judge"))]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _facts(case: dict[str, Any]) -> str:
    exp = case.get("expected", {})
    lines = [f"- {f['label']}: {f['value']}" for f in exp.get("facts", [])]
    for item in exp.get("must_contain", []):
        lines.append("- mentions " + (item if isinstance(item, str) else " or ".join(item)))
    return "\n".join(lines) or "- (see reference)"


async def score(case: dict[str, Any], answer: str) -> dict[str, Any]:
    system, _ = load_prompt("judge")
    user = (f"## Question\n{case['question']}\n\n## Reference answer\n{case['reference_answer']}\n\n"
            f"## Expected facts\n{_facts(case)}\n\n## Generated answer\n{answer or '(empty)'}")
    out = await complete_json("judge", system, user, JudgeScore)
    return {**out.model_dump(), "judge_version": judge_version()}


async def pairwise(case: dict[str, Any], first: str, second: str) -> dict[str, Any]:
    """Which of two answers is closer to the reference — asked twice,
    with the answers swapped. Counts only when both orders agree;
    otherwise the judge was picking a position, not an answer."""
    system, version = load_prompt("judge_pairwise")

    async def ask(a: str, b: str) -> str:
        user = (f"## Question\n{case['question']}\n\n## Reference answer\n{case['reference_answer']}\n\n"
                f"## Answer A\n{a or '(empty)'}\n\n## Answer B\n{b or '(empty)'}")
        return (await complete_json("judge", system, user, PairwiseVerdict)).closer

    forward, backward = await ask(first, second), await ask(second, first)
    unswap = {"A": "B", "B": "A", "tie": "tie"}[backward]
    agreed = forward == unswap
    winner = {"A": "first", "B": "second", "tie": "tie"}[forward] if agreed else "inconsistent"
    return {"winner": winner, "forward": forward, "backward": backward, "judge_version": version}


def passes(scores: dict[str, Any], threshold: int) -> bool:
    return all(scores[d] >= threshold for d in DIMENSIONS)


# ── who judges the judge ─────────────────────────────────────

def weighted_kappa(a: list[int], b: list[int], categories: tuple[int, ...] = (1, 2, 3, 4)) -> float | None:
    """Cohen's kappa with quadratic weights: agreement beyond chance,
    where 3-vs-4 counts as a smaller disagreement than 1-vs-4.
    1 is perfect, 0 is chance. None when undefined (too few labels,
    or no variation at all)."""
    if len(a) != len(b) or len(a) < 2:
        return None
    k = len(categories)
    idx = {c: i for i, c in enumerate(categories)}
    n = len(a)
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        observed[idx[x]][idx[y]] += 1 / n
    pa, pb = Counter(a), Counter(b)
    num = den = 0.0
    for i, ci in enumerate(categories):
        for j, cj in enumerate(categories):
            w = (i - j) ** 2 / (k - 1) ** 2
            num += w * observed[i][j]
            den += w * (pa[ci] / n) * (pb[cj] / n)
    return None if den == 0 else round(1 - num / den, 3)


def agreement(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """pairs: (human scores, judge scores) for the same answer text."""
    out: dict[str, Any] = {"n": len(pairs)}
    for d in (*DIMENSIONS, "pass"):
        if d == "pass":
            h = [int(p[0]["pass"]) for p in pairs]
            j = [int(p[1]["pass"]) for p in pairs]
            exact = sum(x == y for x, y in zip(h, j, strict=True))
            out["pass"] = {"exact_pct": round(100 * exact / len(pairs), 1) if pairs else None,
                           "kappa": weighted_kappa(h, j, (0, 1))}
            continue
        h = [p[0][d] for p in pairs]
        j = [p[1][d] for p in pairs]
        exact = sum(x == y for x, y in zip(h, j, strict=True))
        near = sum(abs(x - y) <= 1 for x, y in zip(h, j, strict=True))
        out[d] = {"exact_pct": round(100 * exact / len(pairs), 1) if pairs else None,
                  "within_one_pct": round(100 * near / len(pairs), 1) if pairs else None,
                  "kappa": weighted_kappa(h, j)}
    return out
