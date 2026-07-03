-- ============================================================
-- Anava Clinic — DB Schema
-- File 20: doctors.clinic_id
-- Denormalized primary-clinic column on doctors, backfilled from
-- clinic_staff_assignments (most-recently-joined active row).
-- clinic_staff_assignments remains the source of truth for
-- multi-clinic doctor membership; this column is a fast-lookup
-- convenience kept in sync at write time (see DoctorRepository.create).
-- ============================================================

ALTER TABLE doctors ADD COLUMN clinic_id UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT;

UPDATE doctors d SET clinic_id = (
    SELECT csa.clinic_id FROM clinic_staff_assignments csa
    WHERE csa.profile_id = d.profile_id AND csa.staff_role = 'doctor' AND csa.is_active = TRUE
    ORDER BY csa.joined_at DESC LIMIT 1
);

CREATE INDEX idx_doctors_clinic_id ON doctors(clinic_id);
