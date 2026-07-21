-- Layer 5 — Compliance schema net-new objects. Maps to the 12 items in the
-- Closure Schema Change Requirements doc, sourced from the Account Closure,
-- Data Retention & Regulatory Compliance Policy (treated as final per approval
-- to proceed, 2026-07-21). Every table/column below cites the policy section
-- it implements.

-- ---------------------------------------------------------------------------
-- 1. erasure_requests + erasure_request_items  (P0 — Section 3.5)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."erasure_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "requested_by" UUID NOT NULL,
    "requester_verification_method" TEXT,
    "status" TEXT NOT NULL DEFAULT 'received',
    "received_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "response_due_at" TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 days'),
    "responded_at" TIMESTAMPTZ,
    "response_summary" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."erasure_requests" IS 'Section 3.5 — a formal patient erasure request. Triggers a classification pass, never immediate bulk deletion.';
COMMENT ON COLUMN compliance."erasure_requests"."status" IS 'received | classified | partially_completed | completed';

CREATE TABLE compliance."erasure_request_items" (
    "item_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "request_id" UUID NOT NULL,
    "data_category" TEXT NOT NULL,
    "bucket" TEXT NOT NULL,
    "legal_basis" TEXT,
    "retention_expires_at" TIMESTAMPTZ,
    "deleted_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."erasure_request_items" IS 'Section 3.5/7.1 — one row per data category evaluated in the classification pass.';
COMMENT ON COLUMN compliance."erasure_request_items"."bucket" IS 'delete_now | retain_locked | compliance_evidence';

-- ---------------------------------------------------------------------------
-- 2. data_portability_requests  (P0 — Section 3.6)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."data_portability_requests" (
    "request_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "patient_id" UUID NOT NULL,
    "requested_by" UUID NOT NULL,
    "format" TEXT NOT NULL DEFAULT 'json',
    "status" TEXT NOT NULL DEFAULT 'pending',
    "delivery_method" TEXT,
    "delivered_at" TIMESTAMPTZ,
    "download_expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."data_portability_requests" IS 'Section 3.6 — structured export request. format: json|pdf. status: pending|generating|delivered|expired.';

-- ---------------------------------------------------------------------------
-- 3. staff_termination_authorizations  (P1 — Section 4.3)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."staff_termination_authorizations" (
    "termination_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "staff_profile_id" UUID NOT NULL,
    "termination_type" TEXT NOT NULL,
    "reason" TEXT,
    "primary_authorizer_id" UUID NOT NULL,
    "secondary_authorizer_id" UUID,
    "authorized_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "effective_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."staff_termination_authorizations" IS 'Section 4.3 — termination_type: voluntary|no_cause|for_cause. secondary_authorizer_id required (app-enforced) only when termination_type=for_cause — two-person authorization.';

-- ---------------------------------------------------------------------------
-- 4. compliance_incidents  (P1 — Section 10)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."compliance_incidents" (
    "incident_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "detected_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "detected_by" UUID,
    "category" TEXT NOT NULL,
    "affected_data_categories" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "affected_patient_count" INTEGER,
    "severity" TEXT,
    "containment_actions" TEXT,
    "board_notified_at" TIMESTAMPTZ,
    "patients_notified_at" TIMESTAMPTZ,
    "eu_authority_notified_at" TIMESTAMPTZ,
    "remediation_summary" TEXT,
    "post_incident_review_at" TIMESTAMPTZ,
    "status" TEXT NOT NULL DEFAULT 'open',
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE compliance."compliance_incidents" IS 'Section 10 — breach/incident record. status: open|contained|notified|closed.';

-- ---------------------------------------------------------------------------
-- 5. manual_snapshots  (P2 — Section 7.3)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance."manual_snapshots" (
    "snapshot_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "purpose" TEXT NOT NULL,
    "created_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "intended_deletion_at" TIMESTAMPTZ NOT NULL,
    "deleted_at" TIMESTAMPTZ
);
COMMENT ON TABLE compliance."manual_snapshots" IS 'Section 7.3 — on-demand RDS/S3 snapshots, tagged with intended deletion date at creation so none are left indefinitely.';
