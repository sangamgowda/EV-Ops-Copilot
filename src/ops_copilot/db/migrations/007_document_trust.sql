-- Every document records who put it in the corpus and how far it is
-- trusted. Retrieval shows the trust level to the agent, and an
-- 'external' document (a web page, a partner's PDF) is never enough on
-- its own to back a causal claim.
--   official  — written and reviewed by the service / product teams
--   internal  — internal reports and notes, not formally reviewed
--   external  — anything from outside the company

ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS trust_level TEXT NOT NULL DEFAULT 'internal';

DO $$ BEGIN
    ALTER TABLE documents ADD CONSTRAINT documents_trust_level_check
        CHECK (trust_level IN ('official', 'internal', 'external'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
