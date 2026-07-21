-- Layer 5 — new columns on existing tables. Cites policy section per column.

-- profiles: anonymisation idempotency marker (Sections 6, 7)
ALTER TABLE core."profiles" ADD COLUMN "is_anonymized" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE core."profiles" ADD COLUMN "anonymized_at" TIMESTAMPTZ;
COMMENT ON COLUMN core."profiles"."is_anonymized" IS 'Bucket 2 anonymisation applied. Purge worker checks this to avoid double-processing.';

-- patients: retention clock, legal hold, closure-state tracking (Sections 3.1, 3.2, 6, 7)
ALTER TABLE core."patients" ADD COLUMN "retention_basis_cleared_at" TIMESTAMPTZ;
ALTER TABLE core."patients" ADD COLUMN "legal_hold" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE core."patients" ADD COLUMN "closure_type" TEXT;
ALTER TABLE core."patients" ADD COLUMN "closure_reason" TEXT;
ALTER TABLE core."patients" ADD COLUMN "closed_at" TIMESTAMPTZ;
ALTER TABLE core."patients" ADD COLUMN "rejoin_deadline" TIMESTAMPTZ;
ALTER TABLE core."patients" ADD COLUMN "portal_access_mode" TEXT NOT NULL DEFAULT 'full';
ALTER TABLE core."patients" ADD COLUMN "last_clinical_contact_at" TIMESTAMPTZ;
COMMENT ON COLUMN core."patients"."retention_basis_cleared_at" IS 'Latest of every linked retention window (7yr clinical, 8yr financial). Anonymisation waits for this, not the earliest window.';
COMMENT ON COLUMN core."patients"."legal_hold" IS 'Active medico-legal case — overrides the normal retention clock (Bucket 2).';
COMMENT ON COLUMN core."patients"."closure_type" IS 'voluntary | dormant | transfer_terminated';
COMMENT ON COLUMN core."patients"."rejoin_deadline" IS 'closed_at + 9 months, set only when closure_type=voluntary. Dormant closures have no rejoin window.';
COMMENT ON COLUMN core."patients"."portal_access_mode" IS 'full | read_only | disabled';
COMMENT ON COLUMN core."patients"."last_clinical_contact_at" IS 'Feeds both the dormancy clock (3mo/1yr) and the 7-year clinical retention clock.';

-- doctors: legal hold (same Bucket 2 override, staff-side — active investigation/litigation)
ALTER TABLE core."doctors" ADD COLUMN "legal_hold" BOOLEAN NOT NULL DEFAULT false;

-- consent_records: guardian consent linkage (Section 3.4)
ALTER TABLE compliance."consent_records" ADD COLUMN "guardian_id" UUID;
COMMENT ON COLUMN compliance."consent_records"."guardian_id" IS 'Set when the signer is a minor patient''s parent/guardian, not the patient themselves.';

-- admins.admin_type already unconstrained TEXT (no CHECK constraint exists in this schema —
-- consistent with the rest of the schema's status columns). 'grievance_officer' is now a
-- documented valid value (Section 11) — no DDL required to "allow" it.
