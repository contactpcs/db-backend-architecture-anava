"""Regression test: a clinical_assistant/receptionist could PATCH their own
row with is_active in the body and self-deactivate (or, if an admin had
deactivated them, self-reactivate) — profiles.is_active is the real login
gate, and the self-edit route had no field-level guard against it. Doctor is
untouched (DoctorUpdate has no is_active field, uses availability_status)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_
from app.core.scoping import assert_staff_self_cannot_toggle_active
from app.modules.staff import schemas as s
from app.modules.staff.router import update_ca, update_receptionist

ME = "00000000-0000-0000-0000-000000000001"


def _self_ctx(role: str) -> RequestContext:
    return RequestContext(user_id=ME, role=role, clinic_id="clinic-A", region_id=None)


@pytest.mark.parametrize("role", ["clinical_assistant", "receptionist"])
def test_self_cannot_toggle_own_is_active(role):
    with pytest.raises(PermissionError_):
        assert_staff_self_cannot_toggle_active(_self_ctx(role), False)


@pytest.mark.parametrize("role", ["clinical_assistant", "receptionist"])
def test_self_edit_without_is_active_is_unaffected(role):
    assert_staff_self_cannot_toggle_active(_self_ctx(role), None)  # no raise


@pytest.mark.parametrize("role", ["clinic_admin", "regional_admin", "super_admin"])
def test_admin_can_still_toggle_is_active(role):
    ctx = RequestContext(user_id="admin-1", role=role, clinic_id="clinic-A", region_id=None)
    assert_staff_self_cannot_toggle_active(ctx, False)  # no raise


@pytest.mark.asyncio
async def test_ca_route_blocks_self_deactivate(monkeypatch):
    ca_id = uuid4()
    profile_id = ME
    ctx = _self_ctx("clinical_assistant")

    from app.modules.staff import router as staff_router

    fake_service = AsyncMock()
    fake_service.get = AsyncMock(return_value={"profile_id": profile_id, "clinic_id": "clinic-A"})
    monkeypatch.setattr(staff_router, "ClinicalAssistantService", lambda db: fake_service)

    body = s.ClinicalAssistantUpdate(is_active=False)
    with pytest.raises(PermissionError_):
        await update_ca(ca_id, body, db=AsyncMock(), ctx=ctx)
    fake_service.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_receptionist_route_blocks_self_deactivate(monkeypatch):
    receptionist_id = uuid4()
    profile_id = ME
    ctx = _self_ctx("receptionist")

    from app.modules.staff import router as staff_router

    fake_service = AsyncMock()
    fake_service.get = AsyncMock(return_value={"profile_id": profile_id, "clinic_id": "clinic-A"})
    monkeypatch.setattr(staff_router, "ReceptionistService", lambda db: fake_service)

    body = s.ReceptionistUpdate(is_active=False)
    with pytest.raises(PermissionError_):
        await update_receptionist(receptionist_id, body, db=AsyncMock(), ctx=ctx)
    fake_service.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_ca_route_allows_self_edit_of_other_fields(monkeypatch):
    ca_id = uuid4()
    profile_id = ME
    ctx = _self_ctx("clinical_assistant")

    from app.modules.staff import router as staff_router

    fake_service = AsyncMock()
    fake_service.get = AsyncMock(return_value={"profile_id": profile_id, "clinic_id": "clinic-A"})
    fake_service.update = AsyncMock(return_value={"ca_id": str(ca_id)})
    monkeypatch.setattr(staff_router, "ClinicalAssistantService", lambda db: fake_service)

    body = s.ClinicalAssistantUpdate(phone="+911234567890")
    result = await update_ca(ca_id, body, db=AsyncMock(), ctx=ctx)
    assert result == {"ca_id": str(ca_id)}
    fake_service.update.assert_awaited_once()
