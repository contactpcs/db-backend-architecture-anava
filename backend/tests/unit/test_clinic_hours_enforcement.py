"""_assert_clinic_operational / _assert_within_clinic_hours (scheduling/
service.py) are what 65_clinic_hours_and_operational_status.sql's schema
actually depends on for enforcement — the DB has no way to cross-validate a
doctor_weekly_schedules row against clinic_weekly_hours itself. No database:
ClinicRepository/ClinicHoursRepository are patched, matching this file's
existing no-DB convention (test_appointments.py)."""

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import BusinessRuleError
from app.modules.scheduling.service import _assert_clinic_operational, _assert_within_clinic_hours

MON, TUE = 0, 1


def _hours_row(day, start="09:00", end="18:00"):
    return {"day_of_week": day, "start_time": dt.time.fromisoformat(start), "end_time": dt.time.fromisoformat(end)}


@pytest.mark.asyncio
async def test_operational_clinic_passes():
    with patch("app.modules.admin.repository.ClinicRepository.get", new=AsyncMock(return_value={"is_operational": True})):
        await _assert_clinic_operational(session=None, clinic_id="c1")  # no raise


@pytest.mark.asyncio
async def test_inactive_clinic_blocks():
    with patch("app.modules.admin.repository.ClinicRepository.get", new=AsyncMock(return_value={"is_operational": False})):
        with pytest.raises(BusinessRuleError) as exc:
            await _assert_clinic_operational(session=None, clinic_id="c1")
        assert exc.value.code == "CLINIC_INACTIVE"


@pytest.mark.asyncio
async def test_unset_clinic_hours_are_ungated():
    """Zero clinic_weekly_hours rows = never configured, not "closed every
    day" — see the migration's rollout-safety note."""
    with patch("app.modules.admin.repository.ClinicHoursRepository.list_for_clinic", new=AsyncMock(return_value=[])):
        await _assert_within_clinic_hours(
            session=None, clinic_id="c1", items=[{"day_of_week": MON, "start_time": dt.time(7, 0), "end_time": dt.time(20, 0)}]
        )  # no raise, however wide the item is


@pytest.mark.asyncio
async def test_item_within_clinic_hours_passes():
    with patch("app.modules.admin.repository.ClinicHoursRepository.list_for_clinic", new=AsyncMock(return_value=[_hours_row(MON)])):
        await _assert_within_clinic_hours(
            session=None, clinic_id="c1", items=[{"day_of_week": MON, "start_time": dt.time(10, 0), "end_time": dt.time(12, 0)}]
        )  # no raise


@pytest.mark.asyncio
async def test_item_outside_clinic_hours_rejected():
    with patch("app.modules.admin.repository.ClinicHoursRepository.list_for_clinic", new=AsyncMock(return_value=[_hours_row(MON)])):
        with pytest.raises(BusinessRuleError) as exc:
            await _assert_within_clinic_hours(
                session=None, clinic_id="c1", items=[{"day_of_week": MON, "start_time": dt.time(7, 0), "end_time": dt.time(12, 0)}]
            )
        assert exc.value.code == "OUTSIDE_CLINIC_HOURS"


@pytest.mark.asyncio
async def test_day_with_no_matching_hours_row_is_implicitly_closed():
    """Clinic has hours for Monday only — a Tuesday slot is rejected, once
    ANY hours row exists (the ungated-if-empty rule no longer applies)."""
    with patch("app.modules.admin.repository.ClinicHoursRepository.list_for_clinic", new=AsyncMock(return_value=[_hours_row(MON)])):
        with pytest.raises(BusinessRuleError) as exc:
            await _assert_within_clinic_hours(
                session=None, clinic_id="c1", items=[{"day_of_week": TUE, "start_time": dt.time(10, 0), "end_time": dt.time(12, 0)}]
            )
        assert exc.value.code == "OUTSIDE_CLINIC_HOURS"
