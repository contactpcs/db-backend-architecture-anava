"""Regression test for the payments/store ownership gap found alongside
clinical's (same eng review) — payments.get_payment and
store.get_store_order/list_store_orders had zero ownership checks;
list_store_orders with no patient_id returned every patient's orders."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_
from app.modules.payments import router as payments_router
from app.modules.store import router as store_router

ME = "00000000-0000-0000-0000-000000000001"
SOMEONE_ELSE = "00000000-0000-0000-0000-000000000002"


def _patient_ctx() -> RequestContext:
    return RequestContext(user_id=ME, role="patient", clinic_id=None, region_id=None)


@pytest.mark.asyncio
async def test_get_payment_blocks_other_patients_payment():
    ctx = _patient_ctx()
    with patch.object(payments_router, "PaymentService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"payment_id": str(uuid4())})
        MockService.return_value.repo.get_owner_profile_id = AsyncMock(return_value=SOMEONE_ELSE)
        with pytest.raises(PermissionError_):
            await payments_router.get_payment(uuid4(), db=AsyncMock(), ctx=ctx)


@pytest.mark.asyncio
async def test_get_payment_allows_own_payment():
    ctx = _patient_ctx()
    with patch.object(payments_router, "PaymentService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"payment_id": str(uuid4())})
        MockService.return_value.repo.get_owner_profile_id = AsyncMock(return_value=ME)
        result = await payments_router.get_payment(uuid4(), db=AsyncMock(), ctx=ctx)
    assert result is not None


@pytest.mark.asyncio
async def test_get_store_order_blocks_other_patients_order():
    ctx = _patient_ctx()
    with patch.object(store_router, "StoreOrderService") as MockService:
        MockService.return_value.get = AsyncMock(return_value={"order_id": str(uuid4()), "patient_id": SOMEONE_ELSE})
        with pytest.raises(PermissionError_):
            await store_router.get_store_order(uuid4(), db=AsyncMock(), ctx=ctx)


@pytest.mark.asyncio
async def test_list_store_orders_forces_own_patient_id_for_patient_role():
    ctx = _patient_ctx()
    with patch.object(store_router, "StoreOrderService") as MockService:
        MockService.return_value.list = AsyncMock(return_value=[])
        # Patient calls with NO patient_id at all — previously returned every order.
        await store_router.list_store_orders(patient_id=None, clinic_id=None, status=None, db=AsyncMock(), ctx=ctx)
    called_kwargs = MockService.return_value.list.call_args.kwargs
    assert str(called_kwargs["patient_id"]) == ME


@pytest.mark.asyncio
async def test_list_store_orders_does_not_force_patient_id_for_staff():
    from app.core.db import RequestContext as RC

    staff_ctx = RC(user_id="receptionist-1", role="receptionist", clinic_id="clinic-A", region_id=None)
    with patch.object(store_router, "StoreOrderService") as MockService:
        MockService.return_value.list = AsyncMock(return_value=[])
        await store_router.list_store_orders(patient_id=None, clinic_id=None, status=None, db=AsyncMock(), ctx=staff_ctx)
    called_kwargs = MockService.return_value.list.call_args.kwargs
    assert called_kwargs["patient_id"] is None
