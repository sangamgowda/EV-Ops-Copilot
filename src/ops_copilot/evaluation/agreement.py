"""Who judges the judge: agreement between the LLM judge and a human.

A person scores answers with the same rubric the judge uses
(scripts/label_eval.py writes golden/human_labels.jsonl). Here the
CURRENT judge re-scores exactly those answer texts, and the two sets of
scores are compared: exact agreement, agreement within one point, and
Cohen's kappa (agreement beyond chance).

The result is saved with the judge's prompt version. Change the judge
prompt and this number no longer describes it — the report says so
instead of quoting a stale figure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ops_copilot.evaluation import judge as judging
from ops_copilot.evaluation.runner import ROOT, load_cases, runs_dir
from ops_copilot.settings import get_config


def labels_path() -> Path:
    p = Path(get_config()["evaluation"]["labels_path"])
    return p if p.is_absolute() else ROOT / p


def load_labels() -> list[dict[str, Any]]:
    p = labels_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


async def measure(on_pair: Any = None) -> dict[str, Any]:
    cfg = get_config()["evaluation"]
    cases = {c["id"]: c for c in load_cases(cfg["golden_path"]) + load_cases(cfg["trap_path"])}
    threshold = cfg["judge_pass_score"]
    pairs, used = [], []
    for label in load_labels():
        case = cases.get(label["id"])
        if case is None:
            continue
        scores = await judging.score(case, label["answer"])
        human = {d: label[d] for d in judging.DIMENSIONS}
        human["pass"] = judging.passes(human, threshold)
        scores["pass"] = judging.passes(scores, threshold)
        pairs.append((human, scores))
        used.append(label)
        if on_pair:
            on_pair(label["id"], human, scores)
    result = {
        "judge_version": judging.judge_version(),
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **judging.agreement(pairs),
        "disagreements": [
            {"id": lab["id"], "human": {d: h[d] for d in judging.DIMENSIONS},
             "judge": {d: j[d] for d in judging.DIMENSIONS}, "judge_notes": j.get("notes")}
            for lab, (h, j) in zip(used, pairs, strict=True) if h["pass"] != j["pass"]
        ],
    }
    runs_dir().mkdir(parents=True, exist_ok=True)
    (runs_dir() / "agreement.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    return result
