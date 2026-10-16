"""Payment audit fixes of 2026-10-16 (Documents/design_payment_audit_fixes.md).
No DB, no gateway."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import BusinessRuleError
from app.integrations import razorpay as rz
from app.modules.payments.service import PaymentService, amount_matches, money, razorpay_receipt


def test_money_is_exact_and_rounds_half_up():  # L6
    assert money(0.1) + money(0.2) == Decimal("0.30")
    assert money("257.505") == Decimal("257.51")


def test_amount_check_compares_paise_and_currency():  # M2
    pay = {"amount": Decimal("515.00"), "currency": "INR"}
    assert amount_matches(pay, {"amount": 51500, "currency": "INR"})
    assert not amount_matches(pay, {"amount": 100, "currency": "INR"})
    assert not amount_matches(pay, {"amount": 51500, "currency": "USD"})
    assert amount_matches(pay, {})  # event shape without an amount is not a mismatch


def test_receipt_ids_fit_razorpay_limit():  # L5
    assert len(razorpay_receipt("appt", uuid4())) <= 40
    assert len(razorpay_receipt("order", uuid4())) <= 40


def test_signatures_fail_closed_without_keys_outside_local():  # M1
    with patch.object(rz, "is_configured", return_value=False), patch.object(rz.settings, "environment", "production"):
        assert rz.verify_webhook_signature(payload=b"{}", signature="x") is False
        assert rz.verify_payment_signature(razorpay_order_id="o", razorpay_payment_id="p", razorpay_signature="s") is False
    with patch.object(rz, "is_configured", return_value=False), patch.object(rz.settings, "environment", "local"):
        assert rz.verify_webhook_signature(payload=b"{}", signature="x") is True


def test_only_razorpay_rejections_count_as_failures():  # H2
    from razorpay.errors import BadRequestError, ServerError

    assert rz.is_gateway_rejection(BadRequestError("bad"))
    assert not rz.is_gateway_rejection(ServerError("5xx"))
    assert not rz.is_gateway_rejection(ConnectionError("timeout"))


@pytest.mark.asyncio
async def test_failed_verify_is_logged_outside_the_request_transaction():  # L2
    svc = PaymentService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.get.return_value = {
        "payment_id": uuid4(),
        "appointment_id": uuid4(),
        "status": "pending",
        "amount": 515,
        "currency": "INR",
        "razorpay_order_id": "order_A",
    }
    svc._get_appointment_for_pay = AsyncMock()
    ctx = RequestContext(user_id=str(uuid4()), role="patient", clinic_id=None, region_id=None)
    with pytest.raises(BusinessRuleError):
        await svc.verify_payment(uuid4(), razorpay_order_id="order_B", razorpay_payment_id="pay", razorpay_signature="sig", ctx=ctx)
    svc.repo.log_event_detached.assert_awaited_once()
    svc.repo.log_event.assert_not_awaited()


def test_production_refuses_to_start_without_razorpay_keys():  # M1
    from app.config import Settings

    with pytest.raises(ValueError, match="razorpay"):
        Settings(
            environment="production",
            signup_hash_secret="x",
            appointment_payment_required=True,
            razorpay_key_id=None,
            razorpay_key_secret=None,
            razorpay_webhook_secret=None,
        )
