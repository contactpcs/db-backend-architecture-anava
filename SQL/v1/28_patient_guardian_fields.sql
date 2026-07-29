-- Under-18 patient registration (self-service or receptionist): no separate
-- guardian identity/table — the patient's own email/phone columns simply
-- hold the guardian's contact (used for login) when the patient is a minor.
-- These two columns just record who that contact belongs to. One guardian
-- per patient only; a second child needs a different contact (accepted
-- tradeoff, not a bug — see PatientService._is_minor / register()).

ALTER TABLE core."patients" ADD COLUMN "guardian_name" TEXT;
ALTER TABLE core."patients" ADD COLUMN "guardian_relationship" TEXT;
