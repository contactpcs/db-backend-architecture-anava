-- ============================================================
-- Anava Clinic — DB Schema
-- File 17: Outbox Events
--
-- Transactional outbox (Architecture doc ADR-006, Section 12/25.2).
-- A domain event row is written in the SAME transaction as the business
-- write it describes — guarantees the event exists if and only if that
-- write actually committed. A relay process (app/workers/event_relay.py)
-- polls published_at IS NULL and publishes to SQS, then stamps published_at.
--
-- LISTEN/NOTIFY trigger below wakes the relay immediately on insert
-- (Section 25.2) instead of relying on poll-interval latency; polling
-- remains as a fallback in case a NOTIFY is missed during a relay restart.
-- ============================================================

CREATE TABLE outbox_events (
    outbox_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type TEXT        NOT NULL,   -- e.g. 'appointment', 'patient', 'payment'
    -- TEXT not UUID — same reasoning as audit_logs.record_id (12_logging_tables.sql):
    -- most tables have UUID PKs, but PRS/anamnesis use TEXT composite keys
    -- (e.g. 'PAT001/001'). A UUID column here rejects those at insert time.
    aggregate_id   TEXT        NOT NULL,
    event_type     TEXT        NOT NULL,   -- e.g. 'appointment_booked' (Section 12 event catalog)
    payload        JSONB       NOT NULL DEFAULT '{}',
    published_at   TIMESTAMPTZ,            -- NULL = not yet relayed to SQS
    publish_attempts INTEGER   NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Relay's main query: "give me unpublished events, oldest first"
CREATE INDEX idx_outbox_unpublished ON outbox_events (created_at) WHERE published_at IS NULL;

CREATE OR REPLACE FUNCTION fn_notify_outbox_event()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('outbox_new_event', NEW.outbox_id::TEXT);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_notify_outbox_event
    AFTER INSERT ON outbox_events
    FOR EACH ROW EXECUTE FUNCTION fn_notify_outbox_event();
