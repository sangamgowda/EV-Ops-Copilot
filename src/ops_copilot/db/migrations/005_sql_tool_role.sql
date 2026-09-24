-- A narrower role for model-written SQL.
--
-- copilot_readonly serves two callers: retrieval, which must read
-- document_chunks.embedding to rank by vector distance, and the SQL
-- tool, which must never read it. Until now only the validator kept
-- the SQL tool away from that column. This role makes the database
-- refuse it too, so a validator bug is a rejected query, not a leak.
--
-- The SQL tool's pool switches to this role when it connects
-- (options "-c role=copilot_sql"). NOLOGIN: nothing can connect as it
-- directly. Switching back needs SET/RESET ROLE or set_config, which
-- the validator refuses as forbidden statements and a blocked function.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_sql') THEN
        CREATE ROLE copilot_sql NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO copilot_sql;

GRANT SELECT ON
    vehicles,
    vehicle_telemetry,
    vehicle_baseline_specs,
    service_events,
    sales_transactions,
    error_codes,
    documents
TO copilot_sql;

-- Column-level grant: every column of document_chunks except the
-- embedding (and tsv, retrieval's search index, which is not in the
-- schema the model sees).
GRANT SELECT (chunk_id, doc_id, chunk_type, section_path, content, error_codes)
    ON document_chunks TO copilot_sql;

GRANT copilot_sql TO copilot_readonly;

-- Session limits enforced by the server, whatever the client sends.
ALTER ROLE copilot_readonly SET default_transaction_read_only = on;
ALTER ROLE copilot_readonly SET lock_timeout = '1s';
-- Pools total well under this; the cap stops a runaway client from
-- taking every connection the primary has.
ALTER ROLE copilot_readonly CONNECTION LIMIT 20;
