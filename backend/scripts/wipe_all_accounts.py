"""One-time data wipe for the Cognito cutover — removes EVERY identity
(staff, admins, patients) and org structure (clinics, regions), plus all
their clinical/scheduling/notification data. Full clean slate. Run this
once, then bootstrap_superadmin_cognito.py to create the first real
account, which then recreates regions/clinics through the app itself.

Every existing profiles.cognito_sub is a local-dev placeholder
('pending-<uuid>' or 'dev-super-admin') with no real Cognito user behind it
— once AUTH_MODE flips to 'cognito', none of these could log in anyway, so
this wipe (rather than a per-account Cognito backfill) is the intentional
choice for a dev database with no real PHI (confirmed with the user before
running this).

Usage: python -m scripts.wipe_all_accounts
"""

import asyncio

from sqlalchemy import text

from app.core.db import get_migration_engine

# Every table that transitively depends on profiles/patients/doctors/CAs/
# receptionists/admins/clinics/regions — clinical records, scheduling,
# requests, logs, notifications, store/payments. CASCADE also reaches
# admins/clinics/regions themselves from here, but they're truncated
# explicitly below too for clarity.
_DEPENDENT_TABLES = """
    activity_logs, appointment_requests, appointments, assessment_protocol_requests,
    ca_doctor_assignments, clinic_requests, clinic_staff_assignments, clinical_assistants,
    consent_records, device_assignments, doctor_patient_assignments, doctor_schedule_overrides,
    doctor_weekly_schedules, doctors, inventory, notifications, patient_clinic_transfers,
    patient_eeg_files, patient_medical_history_files, patients, receptionists, sessions,
    staff_requests, stock_transfers, store_orders, treatment_cycles, anamnesis_assessments,
    appointment_audit_logs, doctor_session_notes, patient_disease_selection,
    patient_scale_assignments, payments, prs_assessment_instances, treatment_plans,
    treatment_sessions, outbox_events, audit_logs
"""


async def main() -> None:
    engine = get_migration_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {_DEPENDENT_TABLES} CASCADE"))
        # admins.region_id/clinic_id and clinics.region_id FK-reference
        # regions/clinics — Postgres blocks TRUNCATE on a table with any
        # existing FK constraint from another table regardless of row
        # count, so CASCADE is required even though everything it would
        # touch is already empty from the truncate above.
        await conn.execute(text("TRUNCATE TABLE admins, clinics, regions CASCADE"))
        await conn.execute(text("DELETE FROM profiles"))

    async with engine.connect() as conn:
        remaining = (await conn.execute(text("SELECT count(*) FROM profiles"))).scalar_one()
    print(f"Wiped. profiles={remaining}")


if __name__ == "__main__":
    asyncio.run(main())
