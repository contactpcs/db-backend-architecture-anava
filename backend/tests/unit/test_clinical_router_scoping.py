"""Regression test for the P0 finding from the eng review: clinical/router.py
had zero ownership checks, so any authenticated patient could read another
patient's treatment cycle/session/plan by UUID. Verifies the fix actually
enforces ownership at the router layer, mocking the service so no DB is
needed."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_
from app.modules.clinical import router as clinical_router

ME = "00000000-0000-0000-0000-000000000001"
SOMEONE_ELSE = "00000000-0000-0000-0000-000000000002"


def _patient_ctx() -> RequestContext:
    return RequestContext(user_id=ME, role="patient", clinic_id=None, region_id=None)


@pytest.mark.asyncio
async def test_get_treatment_plan_blocks_other_patients_plan():
    ctx = _patient_ctx()
    with patch.object(clinical_router, "TreatmentPlanService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"plan_id": str(uuid4()), "patient_id": SOMEONE_ELSE})
        with pytest.raises(PermissionError_):
            await clinical_router.get_treatment_plan(uuid4(), db=AsyncMock(), ctx=ctx)


@pytest.mark.asyncio
async def test_get_treatment_plan_allows_own_plan():
    ctx = _patient_ctx()
    with patch.object(clinical_router, "TreatmentPlanService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"plan_id": str(uuid4()), "patient_id": ME})
        result = await clinical_router.get_treatment_plan(uuid4(), db=AsyncMock(), ctx=ctx)
    assert result["patient_id"] == ME


@pytest.mark.asyncio
async def test_get_cycle_blocks_other_patients_cycle():
    ctx = _patient_ctx()
    with patch.object(clinical_router, "TreatmentCycleService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"cycle_id": str(uuid4()), "patient_id": SOMEONE_ELSE})
        with pytest.raises(PermissionError_):
            await clinical_router.get_cycle(uuid4(), db=AsyncMock(), ctx=ctx)


@pytest.mark.asyncio
async def test_get_session_blocks_other_patients_session():
    ctx = _patient_ctx()
    with patch.object(clinical_router, "SessionService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"session_id": str(uuid4()), "patient_id": SOMEONE_ELSE})
        with pytest.raises(PermissionError_):
            await clinical_router.get_session(uuid4(), db=AsyncMock(), ctx=ctx)


@pytest.mark.asyncio
async def test_list_treatment_plans_forces_own_patient_id_for_patient_role():
    ctx = _patient_ctx()
    with patch.object(clinical_router, "TreatmentPlanService") as MockService:
        MockService.return_value.list = AsyncMock(return_value=[])
        # Patient tries to list someone else's plans by passing another patient_id —
        # the router must override it with ctx.user_id, not trust the query param.
        await clinical_router.list_treatment_plans(patient_id=uuid4(), cycle_id=None, db=AsyncMock(), ctx=ctx)
    called_kwargs = MockService.return_value.list.call_args.kwargs
    assert str(called_kwargs["patient_id"]) == ME


@pytest.mark.asyncio
async def test_list_treatment_plans_does_not_force_patient_id_for_staff():
    from app.modules.clinical.router import RequestContext as RC

    staff_ctx = RC(user_id="doctor-1", role="doctor", clinic_id="clinic-A", region_id=None)
    requested_patient_id = uuid4()
    with patch.object(clinical_router, "TreatmentPlanService") as MockService:
        MockService.return_value.list = AsyncMock(return_value=[])
        await clinical_router.list_treatment_plans(patient_id=requested_patient_id, cycle_id=None, db=AsyncMock(), ctx=staff_ctx)
    called_kwargs = MockService.return_value.list.call_args.kwargs
    assert called_kwargs["patient_id"] == requested_patient_id
