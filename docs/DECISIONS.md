# Design decisions

Each entry records what was decided and why.

---

## 1. Schema-grounded SQL generation

**Decision:** the model writes SQL against an injected schema config;
a deterministic validator decides whether it runs.

**Rationale:** operational questions are open-ended, and generated
SQL covers that range directly. The safety properties — no raw SQL
trust, whitelisted tables, read-only execution — are enforced by the
validator. Query correctness (aggregation choice, date boundaries,
join targets) is verified by running golden-set queries against
seeded data and comparing result rows.

---

## 2. Two ingestion lanes

**Decision:** rows and JSON are *synced* as-is. Only documents are
*ingested*.

**Rationale:** a row is usable the moment it lands; there is nothing
to chunk or embed. Documents need parsing, chunking and embedding.
Each lane does only the work its data requires.

---

## 3. Tools behind an MCP server

**Decision:** tools run in a separate MCP server process.

**Rationale:** database credentials live in the server, not the
agent, so the agent can request a tool call but never holds
credentials. The server is a single point for authorization and
audit, and a second client can reuse the same tools and contract
without duplicating either.

---

## 4. The reflection loop

**Decision:** Plan → Execute → Observe → Reflect, cycling up to 3
times, with Reflect deciding whether to continue.

**Rationale:** a diagnostic question needs several facts that only
become identifiable after seeing earlier results. "Current draw is
36% high" does not say whether the cause is payload or cell
degradation; that takes another query, chosen in light of the first
answer. Reflect runs on the cheap model tier and is skipped for
lookups, and the loop stops as soon as a lap adds nothing new.

---

## 5. Lap count is determined by the question

**Decision:** no lap budget is set in advance. Reflect decomposes
what the question requires and stops when it has it.

**Rationale:** the requirement is a property of the question. A
lookup needs a measurement, so it exits after lap 1. A causal
question needs measurement + baseline + mechanism, so it continues.
A hard iteration cap and a no-new-evidence check bound the loop.

---

## 6. Reflect judges reachability

**Decision:** step 3 of the Reflect prompt asks whether a missing
component is *obtainable*.

**Rationale:** if a tool has already returned nothing for a
component, another lap will return nothing again. That resolves to
`exhausted`, and the agent answers with what it has instead of
repeating the same retrieval.

---

## 7. Empty and failed results are evidence

**Decision:** a tool that fails or returns nothing produces an
`Evidence` entry with an explicit status.

**Rationale:** an absence the agent can see is reportable. This is
what makes "telemetry is available but no documentation covers this
model" a possible answer.

---

## 8. Comparisons computed in code

**Decision:** `delta_pct` and `verdict` are calculated in `observe`.

**Rationale:** arithmetic is exact in code, and a computed verdict
lets Reflect check "do I have a verdict for this component"
deterministically. Baselines come from `vehicle_baseline_specs`,
which the seed script populates.

---

## 9. EXPLAIN cost gate

**Decision:** every query is planned before it is executed and
rejected above a cost budget.

**Rationale:** AST rules check structure; the planner knows cost. A
structurally valid query can still plan a full scan over a very
large table, and asking the planner first catches that before
execution. The budget is set in `config/app_config.yaml`.

---

## 10. LIMIT only on the outermost non-aggregate SELECT

**Decision:** LIMIT injection is conditional.

**Rationale:** a LIMIT does not reduce the work an aggregate does,
and injecting one into a nested CTE would change the result.
Aggregates are bounded by the EXPLAIN gate and the statement
timeout.

---

## 11. Error codes promoted to SQL rows

**Decision:** error-code tables are parsed into `error_codes` at
ingestion.

**Rationale:** `ERR_401` is an exact key, so it is looked up with an
equality match. Tables that do not match the expected shape remain
searchable as document chunks.

---

## 12. Entity id boosts, domain filters

**Decision:** domain is a hard filter; entity id adjusts ranking.

**Rationale:** most service documentation is model-specific rather
than VIN-specific, so boosting on the VIN keeps those documents in
the candidate set while ranking vehicle-specific material first.
Entity resolution runs before search, so typos are corrected
against real rows.

---

## 13. Tiered groundedness

**Decision:** three deterministic tiers, with an LLM check only when
one of them flags a claim.

**Rationale:** unresolvable citations, numbers that appear in no
evidence, and causal language attached to an empty result are all
mechanically detectable. Entailment-level checks — including trap
cases for knowledge the model already has — run in offline
evaluation.

---

## 14. Schema config: generated structure, curated meaning

**Decision:** structure comes from `information_schema`;
descriptions are hand-written and merged back in.

**Rationale:** the generator keeps tables and columns in sync with
the database, and curated descriptions give the model meaning —
`pdc` becomes "Payload Design Capacity, in kg". The generator flags
new columns that need a description. At larger scale the same
retrieval pattern used for documents can select the relevant schema
subset per query.

---

## 15. Drift changes go through review

**Decision:** detected schema or unit drift blocks queries on the
affected column and proposes a config change for review.

**Rationale:** the schema config defines what the model believes
about the data, so changes to it go through the same review as any
other change.
