"""Regression tests for app/core/scoping.py — the app-layer ownership
checks that stand in for RLS (the app DB role bypasses RLS entirely, see
scoping.py's module docstring). Written as part of the eng review that
found clinical/, payments/, and store/ missing these checks."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_
from app.core.scoping import assert_clinic_scope, assert_owns_profile, assert_patient_self


def _ctx(**overrides) -> RequestContext:
    defaults = dict(user_id="00000000-0000-0000-0000-000000000001", role="patient", clinic_id=None, region_id=None)
    defaults.update(overrides)
    return RequestContext(**defaults)


def test_assert_owns_profile_allows_own_record():
    ctx = _ctx(role="patient", user_id="p1")
    assert_owns_profile(ctx, "p1")  # no raise


def test_assert_owns_profile_blocks_other_patients_record():
    ctx = _ctx(role="patient", user_id="p1")
    with pytest.raises(PermissionError_):
        assert_owns_profile(ctx, "someone-elses-profile-id")


@pytest.mark.parametrize("role", ["doctor", "clinical_assistant", "receptionist", "clinic_admin", "super_admin"])
def test_assert_owns_profile_is_noop_for_staff(role):
    ctx = _ctx(role=role, user_id="staff-1")
    assert_owns_profile(ctx, "any-other-profile-id")  # no raise regardless of mismatch


def _session_returning(row: dict | None):
    session = MagicMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_assert_patient_self_blocks_mismatched_patient_id():
    ctx = _ctx(role="patient", user_id="p1")
    session = _session_returning({"profile_id": "someone-else"})
    with pytest.raises(PermissionError_):
        await assert_patient_self(ctx, session, "patients.patient_id-for-someone-else")


@pytest.mark.asyncio
async def test_assert_patient_self_allows_matching_patient_id():
    ctx = _ctx(role="patient", user_id="p1")
    session = _session_returning({"profile_id": "p1"})
    await assert_patient_self(ctx, session, "patients.patient_id-for-p1")  # no raise


@pytest.mark.asyncio
async def test_assert_patient_self_is_noop_for_staff():
    ctx = _ctx(role="doctor", user_id="staff-1")
    session = _session_returning(None)
    await assert_patient_self(ctx, session, "any-patient-id")  # no raise, no DB check needed


@pytest.mark.asyncio
async def test_assert_clinic_scope_blocks_other_clinic_for_clinic_admin():
    ctx = _ctx(role="clinic_admin", user_id="admin-1", clinic_id="clinic-A")
    session = MagicMock()
    with pytest.raises(PermissionError_):
        await assert_clinic_scope(ctx, session, "clinic-B")


@pytest.mark.asyncio
async def test_assert_clinic_scope_allows_own_clinic_for_clinic_admin():
    ctx = _ctx(role="clinic_admin", user_id="admin-1", clinic_id="clinic-A")
    session = MagicMock()
    await assert_clinic_scope(ctx, session, "clinic-A")  # no raise, no DB round trip needed
