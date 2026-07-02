-- ============================================================
-- Anava Clinic — DB Schema
-- File 12: Logging Tables
-- audit_logs, activity_logs
-- RULE: Both tables are append-only. NEVER updated or deleted.
-- ============================================================

-- ------------------------------------------------------------
-- audit_logs — DB trigger change log
-- Written ONLY by database triggers (fn_audit_trigger).
-- No application INSERT/UPDATE/DELETE ever.
--
-- record_id TEXT (not UUID): supports both UUID PKs (most tables)
-- and TEXT PKs (prs_assessment_instances, anamnesis_assessments).
-- changed_by: UUID from app.current_user_id session setting.
--   No FK — log must survive even if profile is deactivated.
-- ------------------------------------------------------------
CREATE TABLE audit_logs (
    log_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name TEXT        NOT NULL,
    operation  TEXT        NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    record_id  TEXT,
    old_data   JSONB,
    new_data   JSONB,
    changed_by UUID,
    clinic_id  UUID,
    ip_address INET,
    request_id TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- activity_logs — application semantic event log
-- Written by application code only (never DB triggers).
-- actor_role denormalized for fast analytics without joins.
-- request_id links all events from a single HTTP request.
-- clinic_id and region_id nullable (some events are system-wide).
-- actor_id ON DELETE RESTRICT: profiles should never be hard-deleted
-- in a healthcare system. Use is_active = FALSE for deactivation.
-- ------------------------------------------------------------
CREATE TABLE activity_logs (
    log_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id    UUID        NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    actor_role  TEXT        NOT NULL,
    request_id  TEXT,
    category    TEXT        NOT NULL CHECK (category IN (
                                'auth', 'admin', 'patient', 'clinical',
                                'appointment', 'assessment', 'consent',
                                'data_access', 'staff', 'store', 'system'
                            )),
    event_type  TEXT        NOT NULL,
    entity_type TEXT,
    entity_id   UUID,
    clinic_id   UUID        REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    region_id   UUID        REFERENCES regions(region_id) ON DELETE RESTRICT,
    metadata    JSONB       NOT NULL DEFAULT '{}',
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
