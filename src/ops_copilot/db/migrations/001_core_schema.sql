-- Core schema. Runs on first container boot.
-- Lane A tables (synced), Lane B tables (ingested), and the
-- operational tables the agent itself writes.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy entity matching

-- ── Lane A: synced from the source system ───────────────────

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id        TEXT PRIMARY KEY,
    model_code        TEXT NOT NULL,
    manufactured_on   DATE,
    registered_region TEXT,
    odometer_km       DOUBLE PRECISION,
    config            JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_vehicles_model ON vehicles (model_code);
CREATE INDEX IF NOT EXISTS idx_vehicles_config ON vehicles USING GIN (config);
-- Trigram index backs fuzzy VIN resolution.
CREATE INDEX IF NOT EXISTS idx_vehicles_id_trgm
    ON vehicles USING GIN (vehicle_id gin_trgm_ops);

CREATE TABLE IF NOT EXISTS vehicle_telemetry (
    id           BIGSERIAL PRIMARY KEY,
    vehicle_id   TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    recorded_at  TIMESTAMPTZ NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    unit         TEXT,
    drive_mode   TEXT
);
-- The composite index the time filter requirement depends on.
CREATE INDEX IF NOT EXISTS idx_telemetry_vehicle_time
    ON vehicle_telemetry (vehicle_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_metric_time
    ON vehicle_telemetry (metric_name, recorded_at DESC);

-- Baselines. Without these a diagnostic question yields a reading,
-- not a diagnosis — there is nothing to compare against.
CREATE TABLE IF NOT EXISTS vehicle_baseline_specs (
    model_code       TEXT NOT NULL,
    drive_mode       TEXT NOT NULL,
    metric_name      TEXT NOT NULL,
    nominal_value    DOUBLE PRECISION NOT NULL,
    tolerance_pct    DOUBLE PRECISION NOT NULL DEFAULT 10.0,
    rated_payload_kg DOUBLE PRECISION,
    PRIMARY KEY (model_code, drive_mode, metric_name)
);

CREATE TABLE IF NOT EXISTS service_events (
    id             BIGSERIAL PRIMARY KEY,
    vehicle_id     TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    occurred_on    DATE NOT NULL,
    event_type     TEXT,
    reported_issue TEXT,
    error_code     TEXT,
    resolution     TEXT
);
CREATE INDEX IF NOT EXISTS idx_service_vehicle ON service_events (vehicle_id, occurred_on DESC);

CREATE TABLE IF NOT EXISTS sales_transactions (
    id             BIGSERIAL PRIMARY KEY,
    vehicle_id     TEXT REFERENCES vehicles(vehicle_id),
    sold_on        DATE NOT NULL,
    region         TEXT,
    channel        TEXT,
    unit_price     NUMERIC(12,2),
    dealer_code    TEXT,
    deal_metadata  JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_transactions (sold_on DESC);
CREATE INDEX IF NOT EXISTS idx_sales_region ON sales_transactions (region, sold_on DESC);

-- Error codes: promoted OUT of the documents into real rows at
-- ingestion. An exact code lookup is a SQL match, not a similarity
-- search. This table is the reason that works.
CREATE TABLE IF NOT EXISTS error_codes (
    code               TEXT PRIMARY KEY,
    subsystem          TEXT,
    meaning            TEXT NOT NULL,
    recommended_action TEXT,
    severity           TEXT,
    source_document    TEXT
);

-- ── Lane B: document ingestion ──────────────────────────────

-- content_hash UNIQUE is what makes re-ingestion idempotent.
CREATE TABLE IF NOT EXISTS documents (
    doc_id            TEXT PRIMARY KEY,
    content_hash      TEXT NOT NULL UNIQUE,
    title             TEXT,
    doc_type          TEXT,
    domain            TEXT,
    applies_to_models TEXT[],
    effective_date    DATE,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id     BIGSERIAL PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    chunk_type   TEXT NOT NULL,
    section_path TEXT,
    content      TEXT NOT NULL,
    error_codes  TEXT[],
    embedding    vector(384),          -- bge-small-en-v1.5
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
-- Dense half of hybrid search.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
-- Sparse half.
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks (doc_id);

-- ── Operational ─────────────────────────────────────────────

-- Evidence summaries live in state; raw payloads live here.
-- A SQL result can be 20kb — its summary is three lines, and only
-- the summary is ever paid for in tokens.
CREATE TABLE IF NOT EXISTS evidence_raw (
    raw_ref    TEXT PRIMARY KEY,
    turn_id    TEXT NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id        TEXT PRIMARY KEY,   -- == langfuse trace id
    session_id     TEXT NOT NULL,
    question       TEXT NOT NULL,
    answer         TEXT,
    domains        TEXT[],
    entities       JSONB,
    iterations     INT,
    stop_reason    TEXT,
    partial        BOOLEAN DEFAULT FALSE,
    prompt_versions JSONB,             -- reproducibility
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns (session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS flagged_interactions (
    id           BIGSERIAL PRIMARY KEY,
    turn_id      TEXT NOT NULL REFERENCES conversation_turns(turn_id),
    rating       TEXT NOT NULL,        -- up | down | implicit_down
    category     TEXT,
    comment      TEXT,
    cluster_id   TEXT,                 -- failure signature
    promoted     BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_flagged_cluster ON flagged_interactions (cluster_id, promoted);
