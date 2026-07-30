-- Receptionist-initiated patient registration (OTP channel-verification,
-- same Cognito wizard self-registration uses — see auth/router.py). Adds
-- who registered a staff-registered patient, alongside the existing
-- self_registered flag (which already captures self-service vs staff).
-- NULL when self_registered = true (the patient registered themselves,
-- nothing to attribute). Set to the receptionist's profiles.id otherwise.
-- audit_logs already records the INSERT itself via changed_by — this
-- column is for fast reads/reporting without joining the audit log.

ALTER TABLE core."patients" ADD COLUMN "registered_by" UUID;
ALTER TABLE core."patients" ADD CONSTRAINT "fk_patients_registered_by" FOREIGN KEY ("registered_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
CREATE INDEX "idx_patients_registered_by" ON core."patients" USING btree ("registered_by") WHERE (registered_by IS NOT NULL);
