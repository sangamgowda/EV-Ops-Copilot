-- The feedback loop needs to read WHY a turn failed without calling
-- the tracing backend: the same outcome fields the trace carries
-- (tools used, evidence statuses, grounding, latency) are stored on the
-- turn row. turn_id is still the trace id, so the full trace stays one
-- click away.

ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS outcome  JSONB;
ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;

-- A flag is reviewed by a person before anything is promoted: they
-- either write what should have happened (promoted) or dismiss it with
-- a reason (the user was wrong, wanted another format, ...).
ALTER TABLE flagged_interactions ADD COLUMN IF NOT EXISTS review_status  TEXT NOT NULL DEFAULT 'new';
ALTER TABLE flagged_interactions ADD COLUMN IF NOT EXISTS review_note    TEXT;
ALTER TABLE flagged_interactions ADD COLUMN IF NOT EXISTS reviewed_at    TIMESTAMPTZ;
ALTER TABLE flagged_interactions ADD COLUMN IF NOT EXISTS golden_case_id TEXT;

CREATE INDEX IF NOT EXISTS idx_flagged_review ON flagged_interactions (review_status, cluster_id);
