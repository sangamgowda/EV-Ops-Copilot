# Architecture

How EV Ops Copilot works, and why it is built the way it is.

---

## The core: an investigation loop

Most question-answering systems look something up once and answer from
that. This one looks at what came back and decides whether it is
enough, and goes round again if it is not:

```
router ─► plan ─► execute ─► observe ─► reflect ─┐
            ▲                                    │
            └──────── not enough yet ────────────┘
                                                 │
                 enough / nothing more to find / lap cap
                                                 ▼
                              synthesize ─► grounding check ─► answer
```

| Step | What it does |
|---|---|
| **Router** | works out the kind of question (diagnostic, business or both; a value, a cause or a comparison) and resolves vehicle IDs against real records |
| **Plan** | chooses what to look up (database, documents or both) and writes the SQL in the same step |
| **Execute** | runs the tools through the tool server |
| **Observe** | turns results into evidence; comparisons against normal values are computed here, in code |
| **Reflect** | decides whether the evidence answers the question, what is missing, and whether another round could find it |
| **Synthesize** | writes the answer, citing the evidence behind each point |
| **Grounding check** | confirms every citation, number and causal claim is supported by the evidence |

The one backward edge, from Reflect to Plan (`route_after_reflect` in
`agent/graph.py`), is what makes this an agent rather than a pipeline.

### Example: "Why did range drop on V-042?"

| Round | Looked up | Found | Reflect's verdict |
|---|---|---|---|
| 1 | current draw against the normal value | 38% above normal | not enough: consumption is high, cause unknown |
| 2 | load and battery health | ~190 kg against a 150 kg rating; battery healthy | not enough: overload implicated, wear ruled out, need documentation |
| 3 | service documents on overload | SB-114: sustained overload raises drive current | enough |

A single-shot system would stop after round 1 with "current draw is
high": an observation, not a diagnosis.

---

## How a request flows

```
browser / API client
      │  POST /chat (streamed)
      ▼
FastAPI ── LangGraph agent ── LLM provider (Groq)
      │           │
      │           └── MCP client ──► MCP tool server ──► PostgreSQL + pgvector
      │                                (holds the only
      │                                 database credentials)
      └── one Langfuse trace per question (OpenTelemetry)
```

The tool server is a separate process. It holds the database
credentials; the agent process and the model never see them.

---

## Design decisions

**One agent for both kinds of question.** Diagnostic and business
questions share one agent; the domain selects the prompt and the tools
in scope. A question can be both, so the domain is a list, never a
single value: forcing a choice would fail exactly the questions that
matter most.

**Tool choice and SQL writing are one model call.** Plan decides it
needs the database and writes the query in the same response. A
separate generation step would double the cost of every lookup.

**Routing is by tool, not by domain.** SQL always goes through
validation; document search goes straight to retrieval. A business
question can need SQL; a diagnostic question usually needs both.

**Validation is code, never a model.** A document retrieved earlier in
the same question could contain instructions that steer the model, so
every generated query is treated as hostile, whatever its origin. See
"Database safety" below.

**Empty results are evidence.** A lookup that fails or finds nothing is
recorded as evidence with that status. An absence the agent can see is
something it can report; an absence it cannot see is something it
fills with a guess.

**Arithmetic is done in code.** Percentage differences and
above/below-normal verdicts are computed in Observe, not by the model.
Models are unreliable at arithmetic, and it makes Reflect's judgement
nearly deterministic.

**Reflect judges whether more is reachable.** If something is missing
because a tool already returned nothing, another round will return
nothing again: that is "exhausted", not "insufficient". Without this,
every unanswerable question would run to the round limit.

**Partial answers are correct answers.** When the data cannot explain
something, the answer reports what was found, names the gap, and points
to the strongest signal without claiming it as the cause.

**Error codes are looked up exactly.** Error-code tables in documents
are copied into a database table when a document is added. Similarity
search is the wrong tool for an exact key.

**A vehicle ID boosts search results; it does not filter them.** Most
documentation is written per model, not per vehicle, and a hard filter
on a mistyped ID would return nothing. IDs are resolved against real
records before searching.

---

## Model use per question

| Call | Step | Model size | When |
|---|---|---|---|
| 1 | Router | small | once |
| 2 | Plan | large | once per round |
| 3 | Reflect | small | once per round (skipped after round 1 for simple lookups) |
| 4 | Synthesize | large | once |
| — | Grounding check | none | code, except rarely for one flagged claim (large) |

Embedding and re-ranking also use models, locally and without
generating text. On the free tier the larger model has a much smaller
daily allowance than the small one, so this split is what keeps the
system inside the free tier at all.

---

## Data and search

- **Two kinds of data.** Database records (vehicles, readings, sales)
  are queried directly. Documents are split into sections that follow
  their headings, embedded, and indexed.
- **Tables stay whole.** A table is never split across sections, so its
  rows keep their meaning.
- **No duplicates.** A document already added is recognised by its
  content hash and skipped.
- **Hybrid search.** Keyword search (Postgres full-text) finds exact
  terms and codes; vector search finds related wording. Results are
  merged, re-ranked by a cross-encoder, and weak matches are dropped.

---

## Database safety

Every model-written query passes three independent layers:

1. **Structure** (sqlglot): exactly one read-only statement; every
   table, column and function on an allowlist, with columns resolved to
   their real tables first so aliases and `*` cannot hide a restricted
   column; joins on declared keys; a bounded time range on large tables;
   a row limit.
2. **Cost**: the planner's estimated cost must be under a budget
   measured on the actual data volume (`scripts/calibrate_cost_budget.py`).
3. **The database itself**: a read-only role that cannot see restricted
   columns, a 5-second statement timeout, and a small connection pool.

---

## Quality

- **Evaluation** (`src/ops_copilot/evaluation/`): a test set of
  generated, hand-written and promoted cases, plus trap questions whose
  correct answer is "the data does not say". Everything countable is
  checked in code; a judge model from a different family scores the
  rest against a reference answer, and its agreement with human labels
  is measured.
- **Feedback loop** (`src/ops_copilot/feedback/`): a thumbs-down, or a
  user asking the same question again within 30 seconds, flags the
  answer. Flags are grouped by failure type in code, reviewed by a
  person, and promoted into the test set, where a regression blocks the
  change.

---

## Settings

- All settings: `config/app_config.yaml`.
- All model instructions: `config/prompts/`, versioned by content hash
  in every trace.
- The database description the model reads:
  `config/schema_config.yaml`, written by hand with plain-English
  descriptions of every table and column.

---

## Code conventions

- **Structured model output is validated.** Every model reply that
  drives a decision (routing, plans, Reflect's verdict, judge scores) is
  parsed into a Pydantic model; free text is never parsed for meaning.
  The one deliberate exception is Synthesize, which streams plain prose:
  the provider returns JSON-mode output in a single piece, so a JSON
  answer could not stream. Its structure is rebuilt in code instead:
  citations from the inline `[eN]` tags (checked by the grounding
  check), confidence from how the loop ended, gaps from Reflect.
- **No secrets in code**: everything comes through `settings.py` and
  `.env`.
- **No thresholds in code**: every limit lives in `config/app_config.yaml`.
- **Type hints throughout**, `async` for anything that does I/O, and
  comments that explain why rather than what.

---

## Built, and described but not built

**Built:** the investigation loop, SQL validation and cost gate, the MCP
tool server, structure-aware chunking, error-code promotion, entity
resolution, hybrid search with re-ranking, content-hash deduplication,
tracing with prompt versions, the evaluation suite with trap cases and
a calibrated judge, the feedback loop, Docker, and streaming.

**Described, not built:** voice input, per-customer data isolation
(tokens plus Postgres row-level security), monitoring and cost
dashboards, schema-drift detection, and semantic caching. These are
deliberately left as designs rather than mock implementations.
