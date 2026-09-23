# EV Ops Copilot — build context

Read this before writing code. It says what is being built, what has
already been decided, and which decisions are not open for revision.

## What this is

An internal agentic assistant for EV operations teams. It answers
two kinds of question against the same data and the same graph:

- **Diagnostic** — "why did range drop on this vehicle last week"
- **Business** — "how are sales tracking this quarter, and why did
  the south region outperform"

It replaces a manual process: today an ops engineer has to find the
developer who worked on a vehicle and ask them in person.

Vendor-neutral by design. **No company name appears anywhere in this
repo** — not in code, comments, docs, commit messages, or test data.

## The one thing that makes it an agent

Everything else is conventional. This is not:

```
router → plan → execute → observe → reflect ─┐
           ▲                                  │
           └──────── not sufficient ──────────┘
                                              │
              sufficient / exhausted / cap ───┘
                            ↓
                  synthesize → groundedness → END
```

`route_after_reflect` in `agent/graph.py` returns `"plan"` or
`"synthesize"`. That single backward edge is the difference between
this and a pipeline. A pipeline answers from the first tool result;
this one looks at what came back and decides whether to go again.

Worked example — "why did range drop on VIN-xxx":

| Lap | Plan | Observed | Reflect |
|---|---|---|---|
| 1 | current draw vs baseline | 15.0A vs 11.0A → 36% high | not enough: consumption high, cause unknown |
| 2 | test overload and cell degradation | payload 92kg vs 75kg rated; cell health 97% normal | not enough: overload implicated, degradation ruled out, need documentation |
| 3 | retrieve service docs on overload | SB-114: sustained overload raises drive current | enough |

A single-shot system stops at lap 1 with "current draw is high" —
an observation, not a diagnosis. Laps 2 and 3 exist only because
Reflect can send the agent back around.

## Decisions already made — do not relitigate

**One agent, not two.** Diagnostic and Business share one
domain-parameterized sub-agent. Domain selects the prompt and tool
scope; it is a parameter, not a separate agent.

**`domains` is a list.** A question can be both. Never collapse it
to a scalar — that forces a false choice on exactly the questions
that matter most.

**Tool selection and SQL generation are ONE LLM call.** Plan decides
it needs the SQL tool and writes the SQL in the same response.
There is no separate generation step.

**Branch by tool type, not by domain.** After Plan: SQL goes through
validation, RAG goes straight to the MCP client. Domain and tool
choice are independent axes. A business question can need SQL; a
diagnostic question routinely needs both tools.

**Validation is code, never a model.** `sql/validator.py` — AST
checks then an EXPLAIN cost gate. The model is not a security
boundary; a poisoned retrieved document can steer generation, so
every query is treated as hostile regardless of origin.

**Empty results are evidence.** A failed or empty tool call becomes
an `Evidence` entry with status EMPTY / BELOW_THRESHOLD / FAILED. An
absence the agent can see is something it can report; an absence it
cannot see is something it invents around.

**Comparisons are computed in code.** `delta_pct` and `verdict` are
calculated in `observe`, never by the model. Models are unreliable
at arithmetic, and it makes Reflect's check near-deterministic.

**Reflect must judge reachability.** Step 3 of its prompt. If
something is missing because a tool already returned nothing, another
lap returns nothing again — that is `exhausted`, not `insufficient`.
Without this the agent spins to the cap on every unanswerable
question.

**Partial answers are correct outputs.** Report what exists, name
the gap, identify the strongest signal without asserting it as the
cause. Never fill a gap with plausible reasoning.

**Error codes are SQL, not RAG.** Promoted into the `error_codes`
table at ingestion. Semantic search is the wrong tool for exact-key
lookup.

**Entity id boosts, it does not filter.** Domain hard-filters.
Entity resolution runs *before* search. A hard VIN filter returns
zero chunks on a typo, and zero chunks is where hallucination starts.

## LLM call budget

A typical turn:

| Call | Node | Tier | Notes |
|---|---|---|---|
| 1 | router | cheap | once, outside the loop |
| 2 | plan | strong | once per lap |
| 3 | reflect | cheap | once per lap, skipped on lap 1 for lookups |
| 4 | synthesize | strong | once, after the loop |
| — | groundedness | none | tiers 1-3 are code |
| (rare) | groundedness tier 4 | strong | only on a flagged claim |

Embedding and reranking are model calls but not generative — worth
naming separately if asked.

Free-tier constraint that drove the tiering: the strong model has a
much smaller daily token budget than the cheap one. Tiering is not
only cost optimisation here; it is what keeps the system inside the
free tier at all.

## Layout

```
config/           thresholds, schema grounding, versioned prompts
src/ops_copilot/
  agent/          state.py, graph.py, nodes/ (one file per node)
  sql/            validator.py — AST + EXPLAIN gate
  rag/            ingest, chunking, embeddings, rerank, retrieve,
                  entity_resolution, hashing
  mcp_server/     separate process; holds DB credentials
  mcp_client/     lives in the agent process
  llm/            provider-agnostic, tier resolution
  db/             engine (two: owner + readonly), migrations
  observability/  Langfuse wrappers, prompt-version hashing
  evaluation/     runner, deterministic, judge, golden/
  feedback/       capture, promote
scripts/          schema generation, seeding, eval runner
```

Each node module's docstring states its contract: reads / returns /
model / never. Follow it.

## Build order

1. db + migrations + seed script — nothing is testable without data
2. sql/validator.py + its tests — pure functions, no dependencies
3. rag ingest → chunking → embeddings → retrieve
4. mcp_server + tools, then mcp_client
5. agent/state.py, then nodes, then graph.py
6. api/routes_chat.py with streaming
7. observability
8. evaluation + golden set
9. feedback + promotion
10. Docker, deploy, README

**Deploy something live by step 6.** A rough version reachable at a
URL is worth more than a polished one that only runs locally.

## Scope — built vs. described

Built: the loop, AST validation + EXPLAIN gate, MCP, structure-aware
chunking, error-code promotion, entity resolution, hybrid search +
rerank, content-hash dedup, Langfuse with prompt hashing, golden set
+ trap cases + calibrated judge, feedback clustering, Docker,
streaming.

Described honestly, not faked: voice interface, multi-tenant
isolation (token + Postgres RLS), monitoring/autoscaling/cost
dashboards, schema-drift profiling, semantic caching.

Saying "I stubbed voice, here is how I would build it" is a better
outcome than a fake implementation. Do not build a mock of anything
in the second list.

## Conventions

- Type hints everywhere; `from __future__ import annotations` at top
- Pydantic for every structured LLM output — never parse free text
- No secrets in code; everything through `settings.py`
- Every threshold in `config/app_config.yaml`, never inline
- Every prompt in `config/prompts/*.md`, hashed into traces
- `async` for anything touching IO
- Comments explain *why*, not *what*
