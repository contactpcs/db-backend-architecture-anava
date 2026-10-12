"""core.final_reports against the real schema (SQL/v1/117): a signed report
can never be changed or deleted, only its signing doctor can write it, and a
patient sees their own reports and nobody else's."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import async_session_factory
from tests.integration.conftest import needs_test_database

pytestmark = needs_test_database


@pytest.fixture
async def people(admin):
    """A clinic with a doctor and two patients, each a bare profile (the
    table's FKs point at profiles only)."""
    ids = {k: str(uuid.uuid4()) for k in ("region", "clinic", "other_clinic", "doctor", "patient", "other_patient")}
    await admin(
        "INSERT INTO regions (region_id, region_name, country, state) VALUES (:id, :name, 'IN', :name)",
        id=ids["region"],
        name=f"r-{ids['region'][:8]}",
    )
    for key in ("clinic", "other_clinic"):
        await admin(
            "INSERT INTO clinics (clinic_id, clinic_code, clinic_name, clinic_type, region_id) "
            "VALUES (:id, :code, 'Test clinic', 'clinic', :region)",
            id=ids[key],
            code=f"T{ids[key][:8]}",
            region=ids["region"],
        )
    for key, role in (("doctor", "doctor"), ("patient", "patient"), ("other_patient", "patient")):
        await admin(
            "INSERT INTO profiles (id, cognito_sub, email, first_name, last_name, role) VALUES (:id, :sub, :email, 'Test', :last, :role)",
            id=ids[key],
            sub=f"test-{ids[key]}",
            email=f"{ids[key]}@example.com",
            last=key,
            role=role,
        )
    return ids


def _row(ids: dict, *, patient: str = "patient", **overrides) -> dict:
    return {
        "report_id": str(uuid.uuid4()),
        "patient_id": ids[patient],
        "clinic_id": ids["clinic"],
        "doctor_id": ids["doctor"],
        "scope": "overall",
        "diagnosis": "MDD",
        "snapshot": json.dumps({"scope": "overall"}),
        "signature": json.dumps({"typed_name": "Test doctor"}),
        "content_hash": "0" * 64,
        **overrides,
    }


_INSERT = (
    "INSERT INTO final_reports (report_id, patient_id, clinic_id, doctor_id, scope, diagnosis, snapshot, signature, content_hash) "
    "VALUES (:report_id, :patient_id, :clinic_id, :doctor_id, :scope, :diagnosis, CAST(:snapshot AS JSONB), "
    "CAST(:signature AS JSONB), :content_hash)"
)


async def _as(role: str, user_id: str, acting_clinic_id: str | None, sql: str, **params):
    """Runs one statement as the API does: the app login, with the caller's
    identity in the transaction's RLS settings."""
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.current_user_id', :uid, true), set_config('app.current_user_role', :role, true), "
                "set_config('app.current_clinic_id', :cid, true)"
            ),
            {"uid": user_id, "role": role, "cid": acting_clinic_id or ""},
        )
        result = await session.execute(text(sql), params)
        return result.mappings().all() if result.returns_rows else None


async def test_the_signing_doctor_can_insert_a_report(people):
    row = _row(people)
    await _as("doctor", people["doctor"], people["clinic"], _INSERT, **row)
    seen = await _as(
        "doctor", people["doctor"], people["clinic"], "SELECT report_id FROM final_reports WHERE report_id = :id", id=row["report_id"]
    )
    assert len(seen) == 1


@pytest.mark.parametrize(
    "role, user, clinic, overrides",
    [
        ("clinical_assistant", "doctor", "clinic", {}),  # only a doctor signs
        ("patient", "patient", None, {}),  # a patient cannot write their own report
        ("doctor", "patient", "clinic", {}),  # signing as somebody else
        ("doctor", "doctor", "other_clinic", {}),  # a report for another clinic
    ],
)
async def test_nobody_else_can_insert_a_report(people, role, user, clinic, overrides):
    with pytest.raises(DBAPIError):
        await _as(role, people[user], people[clinic] if clinic else None, _INSERT, **_row(people, **overrides))


@pytest.mark.parametrize(
    "statement", ["UPDATE final_reports SET diagnosis = 'changed' WHERE report_id = :id", "DELETE FROM final_reports WHERE report_id = :id"]
)
async def test_a_signed_report_cannot_be_changed_or_deleted(admin, people, statement):
    row = _row(people)
    await admin(_INSERT, **row)
    # The app login has no UPDATE/DELETE grant at all ...
    with pytest.raises(DBAPIError):
        await _as("doctor", people["doctor"], people["clinic"], statement, id=row["report_id"])
    # ... and the trigger stops even the admin connection, which bypasses RLS.
    with pytest.raises(DBAPIError, match="immutable"):
        await admin(statement, id=row["report_id"])
    kept = (await admin("SELECT diagnosis FROM final_reports WHERE report_id = :id", id=row["report_id"])).scalar_one()
    assert kept == "MDD"


async def test_blank_diagnosis_is_rejected_by_the_table(admin, people):
    with pytest.raises(DBAPIError):
        await admin(_INSERT, **_row(people, diagnosis="   "))


async def test_a_patient_sees_only_their_own_reports(admin, people):
    mine, theirs = _row(people), _row(people, patient="other_patient")
    await admin(_INSERT, **mine)
    await admin(_INSERT, **theirs)
    both = [mine["report_id"], theirs["report_id"]]
    sql = "SELECT report_id FROM final_reports WHERE report_id = ANY(CAST(:ids AS UUID[]))"

    seen = await _as("patient", people["patient"], None, sql, ids=both)
    assert [str(r["report_id"]) for r in seen] == [mine["report_id"]]


async def test_staff_of_another_clinic_and_non_reading_roles_see_nothing(admin, people):
    row = _row(people)
    await admin(_INSERT, **row)
    sql = "SELECT report_id FROM final_reports WHERE report_id = :id"

    assert len(await _as("clinic_admin", str(uuid.uuid4()), people["clinic"], sql, id=row["report_id"])) == 1
    assert await _as("doctor", str(uuid.uuid4()), people["other_clinic"], sql, id=row["report_id"]) == []
    # Receptionists and clinical assistants do not read final reports.
    assert await _as("receptionist", str(uuid.uuid4()), people["clinic"], sql, id=row["report_id"]) == []
    assert await _as("clinical_assistant", str(uuid.uuid4()), people["clinic"], sql, id=row["report_id"]) == []
