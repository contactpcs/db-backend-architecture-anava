-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS reference;
CREATE SCHEMA IF NOT EXISTS compliance;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS archive;

COMMENT ON SCHEMA core IS 'Operational data — patients, appointments, treatment, clinical records, commerce. High-write, grows forever.';
COMMENT ON SCHEMA reference IS 'Static catalogue/config data — PRS scales/questions, anamnesis catalogue, consent templates, products. Read-heavy, cacheable.';
COMMENT ON SCHEMA compliance IS 'Legal/audit records — audit trail, consent evidence, (future) erasure/portability/incident tables. Write-once, long retention.';
COMMENT ON SCHEMA analytics IS 'De-identified aggregates for business analytics. No PII. Fed by batch ETL from core, one-way.';
COMMENT ON SCHEMA ops IS 'Infrastructure plumbing — outbox events, migration bookkeeping. Not business data.';
COMMENT ON SCHEMA archive IS 'Lifecycle state, not a fixed table set — detached cold partitions awaiting purge/anonymisation window.';
