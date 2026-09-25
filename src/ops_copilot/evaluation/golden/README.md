# Golden dataset

What "better" and "worse" mean for this system: 45 questions with the
behaviour a correct run shows, plus 8 trap questions whose correct
answer is "the data does not say".

## Files

| File | Written by | What it is |
|---|---|---|
| `curated.yaml` | hand | adversarial cases and cases promoted from real failures |
| `traps.yaml` | hand | trap questions and the leaked answers to watch for |
| `golden.jsonl` | `scripts/build_golden.py` | every scored case |
| `trap_cases.jsonl` | `scripts/build_golden.py` | the traps, in case form |
| `golden_meta.json` | `scripts/build_golden.py` | counts, data version, rejected questions |
| `human_labels.jsonl` | `scripts/label_eval.py` | your own scores, for judging the judge |

Rebuild after re-seeding the data (numbers are computed from it):
`python scripts/build_golden.py` — or `--no-generate` to keep the
reviewed document questions and refresh only the numbers.

## Three sources

| Share | Source | Why |
|---|---|---|
| ~58% | synthetic: from seeded data (code) and from whole documents (cheap model) | ground truth known exactly |
| ~31% | hand-written adversarial | nonexistent vehicle and model, typo, a claim to refute, missing metric, ambiguous, cross-domain, held-out documents, launch timing |
| ~11% | promoted from real failures | each names the failure it came from; Phase 13 adds more |

Cases whose correct behaviour is a partial answer or an abstention are
included on purpose (`abstain: true`). A set of only happy paths tests
the easy half.

## Case shape

```json
{
  "id": "syn_overload_V-042",
  "source": "synthetic_data",
  "question": "Why did range drop on V-042 this week?",
  "expected": {
    "domains": ["diagnostic"],
    "vehicle_id": "V-042",
    "tools": ["structured_query_tool", "rag_retrieval_tool"],
    "key_args": [{"tool": "structured_query_tool", "contains": ["V-042", "current_draw"]}],
    "max_iterations": 3,
    "stop_reasons": ["complete", "no_new_evidence"],
    "facts": [{"label": "current draw above baseline pct", "value": 38.1, "tolerance": 6}],
    "must_contain": [["overload", "overloaded"]],
    "must_not_contain": ["caused by battery wear"]
  },
  "reference_answer": "..."
}
```

Every field in `expected` is checked in code (`evaluation/deterministic.py`);
a field a case leaves out is not checked. The judge compares the answer
with `reference_answer`.

## Traps

Questions a language model can answer from training, whose answers were
checked to be absent from every document (`--check-traps` re-checks,
and building fails if one leaks in). The correct answer is abstention.
The **ungrounded-truth rate** — how often the answer contains the
leaked fact anyway — is a headline metric: it catches the right answer
from the wrong source, which citation checks cannot see.

## Leakage in generated questions

1. Questions are generated from the **whole** document, never one chunk.
2. A question sharing more than `max_question_chunk_overlap` (0.6) of its
   content words with any chunk of its document is rejected and
   regenerated; `golden_meta.json` keeps what was rejected.
3. Three documents are **held out**: never shown to the generator,
   covered by hand-written questions only.
4. The report's headline is the **lower** of the synthetic and the
   hand-written pass rates.
