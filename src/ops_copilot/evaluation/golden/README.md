# Golden dataset

Three sources, deliberately mixed:

| Share | Source | Why |
|---|---|---|
| ~60% | synthetic, from seeded data | ground truth is known exactly |
| ~25% | hand-written adversarial | ambiguous, cross-domain, missing evidence, nonexistent VIN, a claim to refute |
| ~15% | promoted from real failures | the feedback loop's output |

Stratified across domain × query_type. Deliberately includes cases
whose **correct** behaviour is a partial answer or an abstention —
not only happy paths.

## Sample shape

```json
{
  "id": "gd_041",
  "question": "...",
  "expected": {
    "domains": ["diagnostic"],
    "query_type": "explain",
    "entities": {"vehicle_id": "..."},
    "tool_calls": [{"tool": "structured_query_tool", "key_args": {"metric": "current_draw"}}],
    "max_iterations": 3,
    "stop_reason": "complete",
    "facts": [{"claim": "current draw ~36% above baseline", "value": 36.4, "tolerance": 2.0}],
    "must_not_contain": ["cell degradation as cause"]
  },
  "reference_answer": "...",
  "source": "curated",
  "tags": ["cross_domain", "partial_evidence"]
}
```

## trap_cases.jsonl

Questions answerable from an LLM's pretrained knowledge, whose
answers were **deliberately excluded** from the seeded knowledge
base. Correct behaviour is abstention.

These exist to catch the failure that citation checking cannot: a
right answer produced from training data rather than from retrieved
evidence. It looks correct, so nothing else flags it.

Report **ungrounded-truth rate** from these as a headline metric
next to accuracy.

## Synthetic generation — avoiding leakage

Generating a question from a chunk makes the question inherit that
chunk's vocabulary, so retrieval finds it trivially and the hit rate
means nothing. Three mitigations:

1. generate from the whole document, not the target chunk
2. reject any question whose word overlap with its expected chunk
   exceeds `max_question_chunk_overlap`
3. hold out documents entirely — some questions target documents
   never used for generation

When synthetic scores 90 and hand-written scores 60, report 60.
