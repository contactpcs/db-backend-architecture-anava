"""Fix RLS same-clinic SELECT policies missing a role guard.

Several SELECT policies (patients, consent_records, prs_assessment_instances,
prs_responses, patient_disease_selection, anamnesis_assessments,
anamnesis_responses, patient_scale_assignments) granted "same clinic"
visibility via a clause with no role check, e.g.
`OR primary_clinic_id = rls_clinic_id()`. core/middleware.py sets
app.current_clinic_id for role='patient' too (to the patient's own
primary_clinic_id), so any patient could see every OTHER patient (and their
anamnesis/PRS/disease-selection/consent records) at the same clinic —
confirmed exploitable via GET /patients as a patient-role token. Matches
SQL/26_fix_rls_patient_clinic_leak.sql — that file is the schema source of
truth per 0001's convention.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-03

"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL"


def upgrade() -> None:
    # asyncpg's prepared-statement protocol rejects multiple commands in one
    # execute() call (unlike 0001's baseline loader, this file has no $$
    # dollar-quoted bodies, so a plain split on top-level ';' is safe) —
    # comment lines are stripped BEFORE splitting, not filtered per-chunk
    # after, since a chunk can be "trailing comment + next real statement"
    # glued together by the split and get wrongly discarded whole.
    sql_text = (SQL_DIR / "26_fix_rls_patient_clinic_leak.sql").read_text(encoding="utf-8-sig")
    code_only = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def downgrade() -> None:
    sql_text = """
        DROP POLICY IF EXISTS rls_patients_select ON patients;
        CREATE POLICY rls_patients_select ON patients FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR (rls_user_role() = 'regional_admin' AND primary_clinic_id IN (
                SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
            ))
            OR primary_clinic_id = rls_clinic_id()
            OR profile_id = rls_user_id()
            OR (rls_user_role() = 'doctor' AND primary_doctor_id = rls_user_id())
        );

        DROP POLICY IF EXISTS rls_cr_select ON consent_records;
        CREATE POLICY rls_cr_select ON consent_records FOR SELECT
        USING (
            rls_user_role() = 'super_admin'
            OR (rls_user_role() = 'regional_admin' AND clinic_id IN (
                SELECT clinic_id FROM clinics WHERE region_id = rls_region_id()
            ))
            OR clinic_id = rls_clinic_id()
            OR patient_id = rls_user_id()
            OR staff_id = rls_user_id()
        );

        DROP POLICY IF EXISTS rls_pai_select ON prs_assessment_instances;
        CREATE POLICY rls_pai_select ON prs_assessment_instances FOR SELECT
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin')
            OR patient_id = rls_user_id()
            OR cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
        );

        DROP POLICY IF EXISTS rls_prs_resp_select ON prs_responses;
        CREATE POLICY rls_prs_resp_select ON prs_responses FOR SELECT
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin')
            OR instance_id IN (
                SELECT instance_id FROM prs_assessment_instances WHERE patient_id = rls_user_id()
            )
            OR instance_id IN (
                SELECT instance_id FROM prs_assessment_instances
                WHERE cycle_id IN (SELECT cycle_id FROM treatment_cycles WHERE clinic_id = rls_clinic_id())
            )
        );

        DROP POLICY IF EXISTS rls_pds_select ON patient_disease_selection;
        CREATE POLICY rls_pds_select ON patient_disease_selection FOR SELECT
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin')
            OR patient_id = rls_user_id()
            OR patient_id IN (
                SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
            )
        );

        DROP POLICY IF EXISTS rls_anamnesis_select ON anamnesis_assessments;
        CREATE POLICY rls_anamnesis_select ON anamnesis_assessments FOR SELECT
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin')
            OR patient_id = rls_user_id()
            OR patient_id IN (
                SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
            )
        );

        DROP POLICY IF EXISTS rls_anar_select ON anamnesis_responses;
        CREATE POLICY rls_anar_select ON anamnesis_responses FOR SELECT
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin')
            OR anamnesis_id IN (
                SELECT anamnesis_id FROM anamnesis_assessments WHERE patient_id = rls_user_id()
            )
            OR anamnesis_id IN (
                SELECT anamnesis_id FROM anamnesis_assessments
                WHERE patient_id IN (
                    SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
                )
            )
        );

        DROP POLICY IF EXISTS rls_psa_select ON patient_scale_assignments;
        CREATE POLICY rls_psa_select ON patient_scale_assignments FOR SELECT
        USING (
            rls_user_role() IN ('super_admin', 'regional_admin')
            OR patient_id = rls_user_id()
            OR assigned_by = rls_user_id()
            OR patient_id IN (
                SELECT profile_id FROM patients WHERE primary_clinic_id = rls_clinic_id()
            )
        );
    """
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)
