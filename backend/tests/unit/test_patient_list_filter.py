"""Pending/rejected self-registrations are requests, not patients: hidden from
patient lists unless the caller asks for them (approval filter, own record,
or the approvals queue's opt-in)."""

import asyncio
import uuid

import pytest

from app.modules.patients.repository import PatientRepository

HIDE = "pt.approval_status NOT IN ('pending', 'rejected')"


class CaptureSession:
    def __init__(self):
        self.sql = ""

    async def execute(self, stmt, params=None):
        self.sql = str(stmt)

        class _R:
            def mappings(self):
                return self

            def all(self):
                return []

        return _R()


def list_sql(**kwargs) -> str:
    session = CaptureSession()
    asyncio.run(PatientRepository(session).list(**kwargs))
    return session.sql


def test_plain_patient_list_hides_unapproved_registrations():
    assert HIDE in list_sql()
    assert HIDE in list_sql(clinic_id=uuid.uuid4())


@pytest.mark.parametrize(
    "kwargs",
    [{"approval_status": "rejected"}, {"profile_id": uuid.uuid4()}, {"include_unapproved": True}],
)
def test_explicit_callers_still_see_them(kwargs):
    assert HIDE not in list_sql(**kwargs)
