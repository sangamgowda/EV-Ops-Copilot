# Design decisions

Each entry: what was decided, what it was decided *against*, and
what it costs. Written this way because the alternative and the cost
are what an interrogation asks about.

---

## 1. Schema-grounded SQL generation, not fixed templates

**Decided:** the model writes SQL against an injected schema config;
a deterministic validator decides whether it runs.

**Against:** a fixed set of parameterized templates where the model
only fills arguments.

**Why:** templates cannot cover the open-ended range of questions
this has to answer. The safety properties that make templates
attractive — no raw SQL trust, whitelisted tables, read-only
execution — are kept by the validator instead.

**Cost:** a schema tells the model what *exists*, not what is
*correct to compute*. `AVG` vs `SUM`, an off-by-one date boundary,
a join on the wrong table — all schema-valid and wrong. Those are
caught by running golden-set queries against seeded data and
diffing result rows, not by inspecting the SQL.

---

## 2. Two pipelines for ingestion, not two stages of one

**Decided:** rows and JSON are *synced* as-is. Only documents are
*ingested*.

**Against:** a single pipeline that copies everything and then
processes it.

**Why:** a row is usable the moment it lands. There is nothing to
chunk or embed. Conflating the two invents a processing step for
data that needs none.

**Cost:** two code paths to maintain.

---

## 3. MCP, not in-process tools

**Decided:** tools run in a separate MCP server process.

**Against:** LangChain tools called directly from the agent.

**Why:** database credentials live in the server, not the agent —
a prompt-injected agent can request a tool call but cannot
exfiltrate credentials. One chokepoint for authz and audit.
Reusable by a second client without duplicating either the tools or
the credentials.

**Cost:** a hop per call, a service to deploy, contract versioning.
For a single agent serving one team this would not be worth it, and
LangChain tools would be the right answer.

---

## 4. The reflection loop

**Decided:** Plan → Execute → Observe → Reflect, cycling up to 3
times, with Reflect deciding.

**Against:** a single-shot pipeline — classify, fetch, answer.

**Why:** a diagnostic question needs several facts that are only
identifiable *after* seeing earlier results. "Current draw is 36%
high" does not tell you whether it is payload or cell degradation;
finding that out requires another query, chosen in light of the
first answer.

**Cost:** more LLM calls per turn and higher latency on complex
questions. Mitigated by tiering Reflect to the cheap model, skipping
it entirely for lookups, and stopping when a lap adds nothing.

---

## 5. Lap count decides itself

**Decided:** no lap budget is set in advance. Reflect decomposes
what the question *requires* and stops when it has it.

**Against:** a classifier predicting "this is a 2-lap question".

**Why:** the requirement is a property of the question. A lookup
needs a measurement — after lap 1 it genuinely has one, so it
exits. A causal question needs measurement + baseline + mechanism,
so it cannot. Adaptiveness falls out of the structure.

**Cost:** depends on Reflect judging well. Guarded by a hard cap
and a no-new-evidence check.

---

## 6. Reflect judges reachability, not just sufficiency

**Decided:** step 3 of the Reflect prompt asks whether a missing
component is *obtainable*.

**Against:** only asking whether evidence is sufficient.

**Why:** without it, a missing document makes the agent loop to the
cap every time, re-running a retrieval that already returned
nothing. With it, "already attempted, returned nothing" resolves to
`exhausted` and the answer is honestly partial.

**Cost:** none meaningful. It is one more instruction in a prompt
that already runs.

---

## 7. Empty and failed results are evidence

**Decided:** a tool that fails or returns nothing produces an
`Evidence` entry with an explicit status.

**Against:** treating absence as absence.

**Why:** an absence the agent can see is reportable. An absence it
cannot see is a hole it fills with plausible reasoning. This is what
makes "I have telemetry but no documentation for this model" a
possible answer.

**Cost:** none.

---

## 8. Comparisons computed in code

**Decided:** `delta_pct` and `verdict` calculated in `observe`.

**Against:** letting the synthesis model do the arithmetic.

**Why:** models are least reliable exactly where precision matters
most. It also lets Reflect check "do I have a verdict for this
component" almost deterministically.

**Cost:** baselines must exist in the data. Seeding
`vehicle_baseline_specs` is not optional — without a baseline there
is no delta, and without a delta there is a reading rather than a
diagnosis.

---

## 9. EXPLAIN cost gate, not just AST rules

**Decided:** every query is planned before it is executed, and
rejected above a cost budget.

**Against:** AST validation alone.

**Why:** AST rules cannot reason about *cost*. A query referencing
only whitelisted tables, joining correctly, with a LIMIT, can still
plan a sequential scan over a hundred million rows. The planner
already knows; asking it is nearly free.

**Cost:** one extra round trip per SQL call. The budget is
workload-specific and needs tuning against real data.

---

## 10. LIMIT only on the outermost non-aggregate SELECT

**Decided:** injection is conditional.

**Against:** blanket LIMIT injection.

**Why:** a LIMIT does not reduce the work an aggregate does — it
still scans everything — and injecting into a nested CTE silently
changes the answer. Being wrong is worse than being slow.

**Cost:** aggregates rely entirely on the EXPLAIN gate and the
statement timeout.

---

## 11. Error codes promoted to SQL rows

**Decided:** error-code tables are parsed into `error_codes` at
ingestion.

**Against:** leaving them as document chunks.

**Why:** semantic search is the wrong tool for exact-key lookup.
`ERR_401` should be an equality match, not a similarity score.

**Cost:** ingestion must recognise the table shape. Brittle against
unusual formats; a parse failure means the codes fall back to RAG.

---

## 12. Entity id boosts, domain filters

**Decided:** domain is a hard filter; entity id adjusts ranking.

**Against:** filtering on entity id.

**Why:** a hard filter on a misspelled VIN returns zero chunks, and
zero chunks is where hallucination starts. Most service
documentation is model-specific rather than VIN-specific anyway, so
a strict VIN filter discards the very documents worth retrieving.

**Cost:** occasionally surfaces a chunk about a different vehicle of
the same model. Acceptable — and usually still relevant.

---

## 13. Tiered groundedness

**Decided:** three deterministic tiers, LLM escalation only on a
flag.

**Against:** an LLM groundedness call on every answer.

**Why:** most violations are mechanically detectable — an
unresolvable citation, a number that appears nowhere, a "because"
attached to an empty retrieval. Paying for a model call to find
those is waste.

**Cost:** does not catch the harder failure — a right answer
produced from pretrained knowledge. That needs entailment checks,
ablation runs, and trap cases, and it lives in evaluation, offline.

---

## 14. Schema config: generated structure, curated meaning

**Decided:** structure from `information_schema`; descriptions
hand-written and merged back in.

**Against:** hand-authoring the whole file, or generating all of it.

**Why:** nobody maintains 200 tables by hand, and no generator knows
that `pdc` means Payload Design Capacity. Split the file by what
each source actually knows.

**Cost:** new columns arrive with empty descriptions. The generator
flags them; a human writes one line.

**At scale:** past ~30 tables, retrieve the relevant subset per
query instead of injecting everything — the same RAG pattern,
pointed at the schema. Deliberately not built: injecting everything
is correct at current scale.

---

## 15. Drift detection opens a PR, never auto-applies

**Decided:** detected schema or unit drift blocks queries on the
affected column and proposes a config change for review.

**Against:** automatically updating the schema config.

**Why:** if drift detection can silently rewrite what the model
believes about the data, a false positive quietly corrupts every
answer after it. A system that can redefine its own ground truth
without review is not one to ship.

**Cost:** a human in the loop, and a short window where the affected
column is unavailable.
