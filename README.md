# EV Ops Copilot

An agentic assistant for electric-vehicle operations teams. Ask it
why a vehicle is underperforming, or how sales are tracking, and it
reasons over telemetry and service documentation to answer —
citing what it used, and saying so when it does not know.

Vendor-neutral. Point it at your own schema and documents.

---

## What makes it an agent

Most "AI assistants" are pipelines: classify the question, fetch
something, answer. This one runs a loop.

```
                    ┌──────────────────────────┐
                    │                          │
   question ──► router ──► PLAN ──► EXECUTE ──► OBSERVE ──► REFLECT
                            ▲                                  │
                            └────── not enough ────────────────┘
                                                               │
                                              enough ──────────┘
                                                  │
                                                  ▼
                                    SYNTHESIZE ──► answer
```

After every tool call it asks itself whether it can actually answer
yet. If not, it works out what is missing and goes again — up to
three laps.

**Why that matters.** Asked "why did range drop on this vehicle?":

| Lap | What it does | What it finds | What it decides |
|---|---|---|---|
| 1 | current draw vs. rated baseline | 36% above nominal | consumption is high — but that is not a cause |
| 2 | check overload and cell health | payload 92kg vs 75kg rated; cells normal at 97% | overload implicated, degradation ruled out |
| 3 | retrieve service documentation | bulletin: sustained overload raises drive current | enough to answer |

A single-shot system stops at lap 1 with *"current draw is high"* —
a reading, not a diagnosis.

---

## Design decisions worth knowing

**Structured and unstructured data, one agent.** A "why" question
needs a number *and* an explanation. Two tools — SQL over telemetry,
hybrid retrieval over documents — and the agent decides which, often
both.

**Generated SQL is never trusted.** The model writes it; a
deterministic validator decides whether it runs. AST parsing
(single statement, SELECT-only, table and column whitelist, joins
must carry an ON clause, time filter required on large tables),
then an `EXPLAIN` cost gate that rejects expensive-but-valid queries
before execution. Underneath: a read-only role on a read replica
with a 5s statement timeout.

**It says when it does not know.** If retrieval finds nothing above
the confidence threshold, that is recorded as evidence — so the
answer reports the measurements it has, names what it could not
establish, and points at the strongest signal without asserting it
as the cause. A knowledge-base gap gets flagged rather than
papered over.

**Exact lookups are not semantic search.** Error-code tables are
promoted out of the documents into real database rows at ingestion.
`ERR_401` is a SQL match, not a similarity score.

**Tables are never split.** Structure-aware chunking keeps tables
whole and emits one chunk per row for lookup tables. Recursive
character splitting shreds a table into fragments that answer
nothing.

**Every claim cites its evidence.** Groundedness checking is tiered:
citation ids must resolve, numbers must trace to a returned value,
and causal language is blocked on claims whose only support is an
empty result. Most of that is plain code, so it costs nothing.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph | explicit state machine; the loop is a conditional edge |
| LLM | Groq (OpenAI-compatible) | free tier, genuinely fast |
| Tools | MCP server, separate process | credentials isolated from the agent; reusable by other clients |
| Store | Postgres + pgvector | relational, JSONB and vectors in one place |
| Embeddings | `BAAI/bge-small-en-v1.5` | 384-dim, CPU, no API cost |
| Reranking | `BAAI/bge-reranker-base` | cross-encoder on a 50-candidate shortlist |
| SQL safety | sqlglot | real AST parsing, not regex |
| Tracing | Langfuse | per-node, per-lap, with prompt-version hashes |
| Serving | FastAPI + SSE | streaming answers |

Model tiering is deliberate: a small model runs classification
(router, reflect), a larger one runs generation (plan, synthesize).
On a free tier this is not just cheaper — it is what keeps the
system inside the quota.

---

## Quick start

```bash
git clone <your-repo-url> && cd ev-ops-copilot

cp .env.example .env
# Paste a free Groq key from https://console.groq.com/keys
# Nothing else needs changing to run locally.

docker compose up --build
```

Then:

```bash
# health
curl localhost:8000/health

# seed a realistic dataset
docker compose exec api python scripts/seed_synthetic_data.py

# ingest documents
curl -X POST localhost:8000/ingest -F "file=@docs/sample_bulletin.md"

# ask something
curl -X POST localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question": "Why did range drop on VIN-1042 last week?"}'
```

### Running locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=src
uvicorn ops_copilot.main:app --reload
```

Embedding and reranking models download on first use (~600MB
combined) and are cached.

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /chat` | ask a question; streams the answer |
| `POST /ingest` | ingest a document (hash-checked, chunked, embedded) |
| `POST /eval` | run the golden set, return a regression report |
| `POST /feedback` | rate an answer by `turn_id` |
| `GET /health` | liveness |

`/eval` never runs inside a request path. LLM-as-judge is offline,
by design.

---

## Configuration

Two files, deliberately separate from `.env`:

**`config/app_config.yaml`** — every threshold someone might ask you
to justify: iteration cap, rerank confidence, EXPLAIN cost budget,
join limits, model tiers. Versioned in git so a change shows up in
review.

**`config/schema_config.yaml`** — what the model is told about your
database. Structure is generated from `information_schema`;
descriptions, units and expected ranges are hand-written and merged
back in. The model never sees a bare column name — `pdc` means
nothing, *"Payload Design Capacity, in kg"* does.

```bash
python scripts/generate_schema_config.py
```

At 200+ tables you would retrieve the relevant subset per query
rather than injecting all of it — the same retrieval pattern,
pointed at the schema. Not needed at smaller scale.

**`config/prompts/*.md`** — every prompt, versioned and hashed into
each trace, so a past failure can be checked for reproducibility
after a prompt change.

---

## Evaluation

```bash
python scripts/run_eval.py
```

Golden set mixes synthetic cases with known ground truth,
hand-written adversarial cases, and cases promoted from real
failures. Deliberately includes questions whose *correct* answer is
a partial one or an abstention.

Checks split by what they actually need:

- **Deterministic** (no LLM): routing, tool selection F1, tool
  arguments, iteration count, numeric facts, citation resolution
- **LLM-as-judge** (calibrated): completeness, correctness, hedging —
  temperature 0, anchored rubric, compared against a reference to
  neutralise verbosity bias, with judge-human agreement measured on
  a hand-labelled subset

**Trap cases** are questions answerable from an LLM's pretrained
knowledge but deliberately absent from the knowledge base. Correct
behaviour is abstention. The resulting *ungrounded-truth rate* is
reported alongside accuracy — it catches the failure that citation
checking cannot, where the answer is right for the wrong reason.

---

## Not implemented

Stated plainly rather than faked:

- **Voice interface** — interface contract designed, not built
- **Multi-tenant isolation** — token-derived tenant + Postgres RLS
  designed; one tenant to test against
- **Monitoring / autoscaling / cost dashboards** — token and latency
  metrics already come from Langfuse; a separate stack is not
  warranted at this scale
- **Schema-drift detection** — expected ranges are in the config;
  the nightly profiling job is designed, not built
- **Semantic caching** — real optimisation, wrong priority

---

## License

MIT
