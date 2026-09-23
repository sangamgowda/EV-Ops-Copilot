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
| 1 | Battery usage vs. normal | Using 36% more power than normal |
| 2 | Load and battery health | Carrying 92 kg (rated for 75 kg); battery is healthy |
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
docker compose exec api python scripts/seed_synthetic_data.py

# add a document
curl -X POST localhost:8000/ingest -F "file=@docs/sample_bulletin.md"

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
| `POST /chat` | ask a question |
| `POST /ingest` | add a document |
| `POST /eval` | run the sample questions and report quality |
| `POST /feedback` | rate an answer |
| `GET /health` | check the service is running |

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
scripts/           setup and test scripts
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
