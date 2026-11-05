-- Immutable audit store.
-- Plain English: a ledger that can only be written to, never edited or erased.
-- The application role may INSERT and SELECT; a trigger rejects UPDATE/DELETE
-- even for the table owner. A daily job copies rows to write-once object storage.

CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.events (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id     TEXT,
    event_type     TEXT NOT NULL,       -- query | retrieval | policy_decision | confirm | answer | denial | feedback | sync
    user_id        TEXT,
    user_groups    TEXT[],
    source         TEXT,                -- confluence | jira | servicenow
    doc_id         TEXT,
    classification TEXT,
    decision       TEXT,                -- allow | deny | n/a
    reason         TEXT,
    details        JSONB NOT NULL DEFAULT '{}'::jsonb,
    archived       BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS events_ts_idx ON audit.events (ts);
CREATE INDEX IF NOT EXISTS events_user_idx ON audit.events (user_id, ts);
CREATE INDEX IF NOT EXISTS events_doc_idx ON audit.events (doc_id);
CREATE INDEX IF NOT EXISTS events_type_idx ON audit.events (event_type, ts);

CREATE OR REPLACE FUNCTION audit.block_changes() RETURNS trigger AS $$
BEGIN
    -- The only permitted change: flipping archived=false -> true (and nothing else).
    IF TG_OP = 'UPDATE'
       AND OLD.archived = false AND NEW.archived = true
       AND (to_jsonb(OLD) - 'archived') = (to_jsonb(NEW) - 'archived') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit.events is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_append_only ON audit.events;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.block_changes();

CREATE OR REPLACE FUNCTION audit.block_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit.events cannot be truncated';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_no_truncate ON audit.events;
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON audit.events
    FOR EACH STATEMENT EXECUTE FUNCTION audit.block_truncate();

-- Handy views for compliance.
CREATE OR REPLACE VIEW audit.denials AS
    SELECT ts, user_id, source, doc_id, classification, reason, request_id
    FROM audit.events WHERE decision = 'deny';

CREATE OR REPLACE VIEW audit.confidential_access AS
    SELECT ts, user_id, source, doc_id, classification, request_id, details->>'query' AS query
    FROM audit.events
    WHERE event_type = 'retrieval' AND decision = 'allow'
      AND classification IN ('confidential', 'highly_confidential', 'restricted');

-- Feedback captured from the web app (thumbs up/down + optional correction).
CREATE TABLE IF NOT EXISTS public.feedback (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id  TEXT,
    user_id     TEXT,
    score       INT,            -- 1 = helpful, 0 = not helpful
    comment     TEXT,
    triage      TEXT NOT NULL DEFAULT 'pending'   -- pending | accepted | rejected (SME review before eval-set admission)
);
