# EV Ops Copilot

An AI assistant for teams that run electric vehicles.

You ask it a question in plain English. It looks at your vehicle
data and your service documents, works out the answer, and tells
you where each part of the answer came from.

---

## What you can ask it

**Vehicle questions**
- "Why did range drop on VIN-1042 last week?"
- "What does error code ERR_401 mean?"

**Business questions**
- "How are sales tracking this quarter?"
- "Which region sold the most last month?"

Some questions are both, and it handles those too.

---

## How it works

It works the way a person investigating a problem would: look at
something, think about what it shows, and decide whether to dig
further.

```
question ─► understand ─► plan ─► look it up ─► review ─┐
                           ▲                            │
                           └──── need more? ────────────┘
                                                        │
                                          got enough ───┘
                                               │
                                               ▼
                                   write answer ─► check answer
```

1. **Understand** — work out what kind of question it is and which
   vehicle, model or region it is about.
2. **Plan** — decide what to look up: numbers from the database,
   information from documents, or both.
3. **Look it up** — fetch the data.
4. **Review** — check whether there is enough to answer. If not,
   go around again with a more focused question (up to 3 times).
5. **Write the answer** — explain what it found, pointing to the
   data behind each point.
6. **Check the answer** — make sure every number and reference in
   the answer really came from the data.

### Example

*"Why did range drop on this vehicle?"*

| Round | What it checks | What it finds |
|---|---|---|
| 1 | Battery usage vs. normal | Using 33% more power than normal |
| 2 | Load and battery health | Carrying about 190 kg (rated for 150 kg); battery is healthy |
| 3 | Service documents | A bulletin explains that overloading increases power use |

**Answer:** the vehicle is regularly overloaded, which increases
power use and reduces range.

---

## What it uses

| Source | Examples |
|---|---|
| **Database** | vehicle details, sensor readings, normal values for each model, service history, sales |
| **Documents** | service bulletins, manuals, help articles |

Error-code tables inside documents are copied into the database
when a document is added, so looking up a code is quick and exact.

---

## Key features

- **Checks its own progress** — decides for itself whether it needs
  to look further before answering.
- **Shows its sources** — every point in the answer links to the
  data it came from.
- **Clear about gaps** — if something can't be found, the answer
  says so.
- **Safe database access** — database queries are checked
  automatically before they run, and the assistant can only read
  data, never change it.
- **Handles typos** — a mistyped vehicle ID is matched to the
  closest real one.
- **Streams answers** — the reply appears word by word as it is
  written.
- **Tested** — a set of sample questions with known answers is used
  to measure quality over time.
- **Works with your own data** — no company-specific setup; point it
  at your own database and documents.

---

## Data

The project ships with a realistic **made-up** dataset, so it can be
run and tested without any real company data.

### Load it

```bash
# into the database started by docker compose
docker compose exec api python scripts/seed_synthetic_data.py --reset

# or from your own machine (Postgres on localhost:5432)
python scripts/seed_synthetic_data.py --reset

# bigger or smaller
python scripts/seed_synthetic_data.py --vehicles 300 --days 90 --reset

# CSV files only, no database needed
python scripts/seed_synthetic_data.py --no-db --csv-dir data/processed
```

The same settings always produce the same data (`--seed` changes
it). A ready-made copy of the default dataset is in `data/sample/`
as compressed CSV files.

### What is in it

With the default 50 vehicles and 30 days:

| Table | Rows | What it holds |
|---|---|---|
| vehicles | 50 | four scooter models, eight cities, private and fleet use |
| vehicle_baseline_specs | 84 | normal values for each model and riding mode |
| vehicle_telemetry | ~200,000 | speed, power draw, battery, temperature and load, every 5 minutes while riding |
| service_events | ~140 | service visits and customer complaints over 12 months |
| sales_transactions | ~19,600 | two years of sales across four regions |
| error_codes | 14 | trouble codes, read from the service manual |

Model specs and monthly sales volumes follow publicly reported
figures for electric scooters in India, with brand names replaced by
neutral codes (`data/reference/ev_models.yaml`).

### Built-in test cases

Some vehicles have a known problem planted in their data, so answers
can be checked:

| Vehicle | Problem | Explained by |
|---|---|---|
| VIN-1042, VIN-1007, VIN-1029 | overloaded: power use up ~33%, range down ~25% | SB-114 |
| VIN-1017, VIN-1036 | battery wearing out: health falling from 92% to 84% | SB-121 |
| vehicles on firmware 2.6.0 | motor runs ~12 °C hotter in sport mode | SB-127 |
| VIN-1023 | power use up ~30% on a model with no documents | nothing — the right answer says so |

Full details, with measured numbers, are written to
`data/seed_manifest.json` each time the data is loaded.

### Documents

`data/documents/` holds made-up service bulletins, manuals, a help
article and business reports, ready to add with `POST /ingest`.

### Collecting web pages

`scripts/collect_web_data.py` downloads public pages you list (spec
sheets, reviews, sales reports), pulls out their text, tables and
key numbers, and can turn each page into a document for
`POST /ingest`. It follows each site's robots.txt rules and waits
between requests. An optional find-and-replace file swaps real names
for neutral ones.

```bash
cp data/sources.example.yaml data/sources.yaml          # list your pages
cp data/anonymize.example.yaml data/anonymize.local.yaml  # optional
make collect
```

---

## Built with

| Part | Tool |
|---|---|
| Agent workflow | LangGraph |
| AI models | Groq (free tier) — a small model for quick decisions, a larger one for writing |
| Database | PostgreSQL with pgvector |
| Document search | keyword + meaning-based search, then re-ranking |
| Tool access | MCP server (a separate service that holds the database connection) |
| Query checking | sqlglot |
| Tracing | Langfuse |
| API | FastAPI |

---

## Getting started

```bash
git clone <your-repo-url> && cd ev-ops-copilot

cp .env.example .env
# Add a free Groq API key from https://console.groq.com/keys

docker compose up --build
```

Then:

```bash
# check it is running
curl localhost:8000/health

# load sample data
docker compose exec api python scripts/seed_synthetic_data.py --reset

# add a document
curl -X POST localhost:8000/ingest -F "file=@data/documents/SB-114_sustained_overload.md"

# ask a question
curl -X POST localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question": "Why did range drop on VIN-1042 last week?"}'
```

### Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=src
uvicorn ops_copilot.main:app --reload
```

---

## API

| Endpoint | What it does |
|---|---|
| `POST /chat` | ask a question; the answer streams back as it is written |
| `POST /ingest` | add a document (markdown, text or PDF) |
| `POST /eval` | run the sample questions and report quality *(arrives with the evaluation phase)* |
| `POST /feedback` | rate an answer, using the `turn_id` from `/chat` |
| `GET /health` | check the service and each thing it depends on |

`/chat` streams [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events):

| Event | Carries |
|---|---|
| `progress` | one plain sentence per step: "Checking current_draw and payload against normal values" |
| `token` | the next piece of the answer |
| `reset` | discard the answer shown so far; a corrected one follows |
| `done` | the answer, citations, confidence, gaps, evidence and the `turn_id` |
| `error` | the turn failed, and why |

Send `"stream": false` for a single JSON reply instead. A turn is capped
at 45 seconds (`config/app_config.yaml`); at the cap it answers from what
it found so far, marked partial. Behind a reverse proxy, streaming needs
buffering off (for nginx, `proxy_buffering off;` — the API also sends
`X-Accel-Buffering: no`).

---

## Configuration

| File | What it holds |
|---|---|
| `.env` | API keys and passwords |
| `config/app_config.yaml` | settings such as how many rounds to allow and search limits |
| `config/schema_config.yaml` | plain-English descriptions of the database, so the AI understands it |
| `config/prompts/` | the instructions given to the AI at each step |

---

## Project layout

```
config/            settings, database descriptions, AI instructions
src/ops_copilot/
  agent/           the question-answering workflow
  sql/             database query checking
  rag/             document processing and search
  mcp_server/      tool service
  mcp_client/      connects the agent to the tool service
  llm/             AI model access
  db/              database setup
  api/             web endpoints
  observability/   tracing
  evaluation/      quality testing
  feedback/        user ratings
data/              reference figures, sample documents, sample dataset
scripts/           data generation, web collection, setup and test scripts
tests/             automated tests
docs/              design notes
```

---

## Status

Under active development. The database setup, query checking,
workflow structure and configuration are in place; the remaining
steps are being built next.

## Roadmap

- Voice input
- Separate data per customer
- Usage and cost dashboards
- Automatic detection of database changes
- Answer caching

---

## License

MIT
