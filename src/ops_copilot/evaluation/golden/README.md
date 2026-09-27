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

## Manual checks

Questions for trying the system by hand against the sample dataset
(`docs/SAMPLE_DATASET.md`), with what a correct answer contains. Numbers
are from the default load and shift slightly with other seed settings.
The automated versions of these, with exact tolerances, are in
`golden.jsonl`.

### Vehicle scenarios

| Question | A correct answer |
|---|---|
| Why did range drop on V-042 this week? | Overloaded: ~190 kg against a 150 kg limit, current draw ~38% above normal, battery healthy (SB-114). |
| Why won't V-012 go above 45 km/h in Sonic mode? | Firmware 3.2.0 applies the Eco X limit to Sonic and Sonic X; speed ~42 against 62 normal. Fix: 3.2.1 (SB-135). |
| Why is V-064 taking so long to charge? | Charger delivering ~0.34 kW instead of ~0.75; battery fine. Replace the charger (SB-140). |
| Why is the range on V-017 getting shorter every week? | Battery wear: cell health down to ~84%, current normal. Balance charge, then capacity test (SB-121). |
| Why is V-023 using more power than normal? | Current ~30% high; overload and battery wear ruled out; no document explains it. Must not claim a cause. |
| How is V-001 doing against its normal values this week? | Within normal ranges: V-001 has no planted fault. |

### Specifications and procedures

| Question | A correct answer |
|---|---|
| What ride modes does the Volt 1 Ultra have and how fast does each go? | Eco X 45, Eco 50, Ride 70, Air 90, Sonic 100, Sonic X 115 km/h. |
| How long does the removable battery take to charge to 80%? | About 2 h 7 min (fixed pack about 3 h 47 min). |
| How much does the Volt 1 Gen 2 cost? | ₹1,45,000 ex-showroom. |
| What does combined braking (CBS) do? | The rear lever brakes both wheels together. |

### Error codes

| Question | A correct answer |
|---|---|
| What does ERR_205 mean? | Ride-mode speed limit mismatch; update firmware 3.2.0 to 3.2.1. |
| What does ERR_403 mean? | Cell health below 85%, critical; book a pack capacity test. |

### Business

| Question | A correct answer |
|---|---|
| Why did the south outsell the other regions between July and September 2026? | The Ultra launched in the south on 1 June, ten weeks before elsewhere; south sales rose from ~2,060 (Q2) to ~2,270 (Q3) while the rest stayed flat. |
| Which model sold the most in 2026? | Volt 1 Gen 2 (~6,000), ahead of Volt 1 (~4,700). |
| Which model has sold the most of all time? | Volt 1 (~10,000): it was the only model for its first eight months. |
| What is the maximum discount on a fleet deal? | 10%, for 25 units or more. |

### Questions it must not invent an answer to

| Question | A correct answer |
|---|---|
| Why did range drop on V-999? | There is no vehicle V-999. |
| Why did range drop on V42? | Treats it as V-042 (same vehicle, different spelling). |
| What is the top speed of the Volt 2? | There is no such model. |

## Running on the free tier

The free tier allows about 200,000 tokens a day on the larger model and
one question uses roughly 8,000-15,000, so a full run spans more than a
day: it stops cleanly at the limit and `run_eval.py --resume <run id>`
finishes it. One question also needs more tokens per minute than a free
model allows, so evaluation lets a throttled question wait up to 120 s
(the app itself stops at 45 s) and the report says how many cases were
slowed.

