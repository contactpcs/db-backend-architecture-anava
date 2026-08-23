"""Regression test for the ownership check on protocol_instances (58).

58 retired core.treatment_cycles/treatment_plans/treatment_sessions and their
clinical/ router endpoints in favour of protocol_instances directly. Those
retired endpoints had a router-level ownership check (any authenticated
patient could otherwise read another patient's cycle/plan by UUID, or list by
passing someone else's patient_id) — this test asserts the same protection
now holds on protocol_instances, so retiring the old module didn't silently
drop it. Mocks the service so no DB is needed."""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_
from app.modules.treatment_protocols import router as protocol_router

ME = "00000000-0000-0000-0000-000000000001"
SOMEONE_ELSE = "00000000-0000-0000-0000-000000000002"


def _patient_ctx() -> RequestContext:
    return RequestContext(user_id=ME, role="patient", clinic_id=None, region_id=None)


@pytest.mark.asyncio
async def test_get_protocol_instance_blocks_other_patients_instance():
    ctx = _patient_ctx()
    with patch.object(protocol_router, "ProtocolInstanceService") as MockService:
        MockService.return_value.get_or_404 = AsyncMock(return_value={"instance_id": str(uuid4()), "patient_id": SOMEONE_ELSE})
        with pytest.raises(PermissionError_):
            await protocol_router.get_protocol_instance(uuid4(), db=AsyncMock(), ctx=ctx)


@pytest.mark.asyncio
async def test_get_protocol_instance_allows_own_instance():
    ctx = _patient_ctx()
    with patch.object(protocol_router, "ProtocolInstanceService") as MockService:
        MockService.return_value.get_or_404 = AsyncMock(return_value={"instance_id": str(uuid4()), "patient_id": ME})
        result = await protocol_router.get_protocol_instance(uuid4(), db=AsyncMock(), ctx=ctx)
    assert result["patient_id"] == ME


@pytest.mark.asyncio
async def test_list_protocol_instances_forces_own_patient_id_for_patient_role():
    ctx = _patient_ctx()
    with patch.object(protocol_router, "ProtocolInstanceService") as MockService:
        MockService.return_value.list = AsyncMock(return_value=[])
        # Patient tries to list someone else's instances by passing another
        # patient_id — the router must override it with ctx.user_id, not trust
        # the query param.
        await protocol_router.list_protocol_instances(patient_id=uuid4(), status=None, skip=0, limit=50, db=AsyncMock(), ctx=ctx)
    called_kwargs = MockService.return_value.list.call_args.kwargs
    assert called_kwargs["patient_id"] == UUID(ME)


@pytest.mark.asyncio
async def test_list_protocol_instances_does_not_force_patient_id_for_staff():
    from app.modules.treatment_protocols.router import RequestContext as RC

    staff_ctx = RC(user_id="doctor-1", role="doctor", clinic_id="clinic-A", region_id=None)
    requested_patient_id = uuid4()
    with patch.object(protocol_router, "ProtocolInstanceService") as MockService:
        MockService.return_value.list = AsyncMock(return_value=[])
        await protocol_router.list_protocol_instances(
            patient_id=requested_patient_id, status=None, skip=0, limit=50, db=AsyncMock(), ctx=staff_ctx
        )
    called_kwargs = MockService.return_value.list.call_args.kwargs
    assert called_kwargs["patient_id"] == requested_patient_id
