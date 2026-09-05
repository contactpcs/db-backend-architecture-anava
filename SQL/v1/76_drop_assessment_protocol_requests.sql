-- 76_drop_assessment_protocol_requests.sql
--
-- APPLY ORDER: after 75. Depends on 05/09/11/12/15/16/17 (core.assessment_protocol_requests)
-- and 58 (which already dropped its cycle_id column/FK/index and rewrote rls_apr_select).
--
-- THE PROBLEM
--
-- core.assessment_protocol_requests backs a CA-submits -> doctor-authorizes
-- clinical workflow (clinical/router.py's four /assessment-protocol-requests
-- endpoints, ProtocolRequestService/Repository). The table has been empty
-- since go-live and the frontend (prs-neurowellness) never built a UI for
-- it — confirmed by exhaustive search of both repos, no page/service/hook
-- references it anywhere, unlike its structural siblings staff_requests/
-- clinic_requests which are fully wired. Dead end-to-end: no data, no UI,
-- decision made to remove it rather than build the missing frontend.
--
-- SCOPE
--
-- Drop the table and everything that points at or lives on it: 3 RLS
-- policies, 2 triggers (audit + updated_at), 5 indexes, 4 remaining FKs
-- (cycle_id's FK/column already gone via 58), the PK, then the table itself.
-- No other table has an FK pointing INTO assessment_protocol_requests
-- (verified against 11_foreign_keys.sql) — this is a clean leaf-table drop,
-- no cascade risk to unrelated tables.
--
-- APPLICATION CODE — lands in the same change as this file
--
--   1. backend/app/modules/clinical/router.py — the 4 protocol-request
--      routes removed (POST/GET/GET/PATCH .../assessment-protocol-requests*).
--   2. backend/app/modules/clinical/service.py — ProtocolRequestService removed.
--   3. backend/app/modules/clinical/repository.py — ProtocolRequestRepository removed.
--   4. backend/app/modules/clinical/schemas.py — ProtocolRequestCreate/Decision/Read removed.
--   5. backend/scripts/wipe_all_accounts.py — "assessment_protocol_requests"
--      removed from _DEPENDENT_TABLES (would error truncating a dropped table).
--
-- If this file is ever applied to a database whose app code predates these
-- changes, the app will fail at runtime (relation does not exist) on any of
-- the 4 removed routes until the matching code deploys — schema and code
-- must land together.

BEGIN;

-- ###########################################################################
-- 1  RLS policies (must go before the table; DROP TABLE would cascade these
--    anyway, but explicit here for the same auditability reason as 58 §3)
-- ###########################################################################

DROP POLICY IF EXISTS "rls_apr_insert" ON core."assessment_protocol_requests";
DROP POLICY IF EXISTS "rls_apr_select" ON core."assessment_protocol_requests";
DROP POLICY IF EXISTS "rls_apr_update" ON core."assessment_protocol_requests";

-- ###########################################################################
-- 2  Triggers
-- ###########################################################################

DROP TRIGGER IF EXISTS trg_audit_assessment_protocol_requests ON core."assessment_protocol_requests";
DROP TRIGGER IF EXISTS trg_updated_at_assessment_protocol_requests ON core."assessment_protocol_requests";

-- ###########################################################################
-- 3  Indexes (idx_apr_cycle_id already gone via 58's DROP COLUMN cycle_id)
-- ###########################################################################

DROP INDEX IF EXISTS core.idx_apr_ca_id;
DROP INDEX IF EXISTS core.idx_apr_doctor_id;
DROP INDEX IF EXISTS core.idx_apr_patient_id;
DROP INDEX IF EXISTS core.idx_apr_status;

-- ###########################################################################
-- 4  Foreign keys (fk_assessment_protocol_requests_cycle_id already gone via 58)
-- ###########################################################################

ALTER TABLE core."assessment_protocol_requests" DROP CONSTRAINT IF EXISTS "fk_assessment_protocol_requests_patient_id";
ALTER TABLE core."assessment_protocol_requests" DROP CONSTRAINT IF EXISTS "fk_assessment_protocol_requests_clinical_assistant_id";
ALTER TABLE core."assessment_protocol_requests" DROP CONSTRAINT IF EXISTS "fk_assessment_protocol_requests_doctor_id";
ALTER TABLE core."assessment_protocol_requests" DROP CONSTRAINT IF EXISTS "fk_assessment_protocol_requests_clinic_id";

-- ###########################################################################
-- 5  Primary key + table
-- ###########################################################################

ALTER TABLE core."assessment_protocol_requests" DROP CONSTRAINT IF EXISTS "assessment_protocol_requests_pkey";

DROP TABLE IF EXISTS core."assessment_protocol_requests";

COMMIT;

-- ###########################################################################
-- VERIFY — run after COMMIT
-- ###########################################################################
--
-- SELECT to_regclass('core.assessment_protocol_requests');  -- NULL
-- SELECT conname FROM pg_constraint WHERE conname LIKE '%assessment_protocol_requests%';  -- 0 rows
-- SELECT indexname FROM pg_indexes WHERE indexname LIKE 'idx_apr_%';  -- 0 rows
-- SELECT policyname FROM pg_policies WHERE tablename = 'assessment_protocol_requests';  -- 0 rows
