-- Guardian details captured during demographics (self-registration and
-- receptionist registration, same shared wizard steps): name, relationship,
-- and now contact as its own field — separate from the patient's own
-- email/phone (the Cognito login channel), which for a minor already holds
-- the guardian's number by design (see 28_patient_guardian_fields.sql).
-- Allowed to duplicate that login contact; not the same column on purpose,
-- since a minor's login channel could be their own phone while the
-- guardian's contact is recorded independently for records/emergency use.

ALTER TABLE core."patients" ADD COLUMN "guardian_contact" TEXT;
