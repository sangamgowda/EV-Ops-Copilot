-- The read-only role the SQL tool connects as.
--
-- This is the layer beneath AST validation and the EXPLAIN gate.
-- If a query somehow got past both, it still cannot write, because
-- this role has no grant that would let it. Defence in depth means
-- the last layer does not depend on the first two being correct.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_readonly') THEN
        -- No password here: 004_readonly_password.sh sets it from
        -- DB_READONLY_PASSWORD, so .env is the only place it lives.
        CREATE ROLE copilot_readonly LOGIN;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE opscopilot TO copilot_readonly;
GRANT USAGE ON SCHEMA public TO copilot_readonly;

-- SELECT only, and only on tables the agent has business reading.
GRANT SELECT ON
    vehicles,
    vehicle_telemetry,
    vehicle_baseline_specs,
    service_events,
    sales_transactions,
    error_codes,
    documents,
    document_chunks
TO copilot_readonly;

-- Operational tables stay out of reach.
REVOKE ALL ON evidence_raw, conversation_turns, flagged_interactions
    FROM copilot_readonly;

-- Queries die at 5s rather than running the connection pool dry.
ALTER ROLE copilot_readonly SET statement_timeout = '5s';
ALTER ROLE copilot_readonly SET idle_in_transaction_session_timeout = '10s';

-- Nothing created later is granted by default.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM copilot_readonly;
