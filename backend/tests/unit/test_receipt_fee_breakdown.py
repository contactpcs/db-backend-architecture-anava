"""build_receipt_pdf branches on whether base_fee_amount is set (64_fee_
breakdown_and_cancellation_policy.sql payments columns) — smoke-test both
branches render without crashing and pull in the extra rows."""

import datetime as dt

from app.modules.payments.receipt import build_receipt_pdf

APPOINTMENT = {
    "patient_name": "Test Patient",
    "doctor_name": "Dr. Test",
    "appointment_type": "initial",
    "appointment_date": dt.date(2026, 8, 27),
    "start_time": dt.time(10, 0),
}
CLINIC = {"clinic_name": "Test Clinic", "address": None, "city": None, "state": None, "phone": None}


def _base_payment(**overrides):
    return {
        "payment_id": "11111111-2222-3333-4444-555555555555",
        "amount": 1100.0,
        "currency": "INR",
        "paid_at": dt.datetime(2026, 8, 27, 9, 0),
        "payment_method": "razorpay_checkout",
        "razorpay_payment_id": "pay_test123",
        "status": "paid",
        **overrides,
    }


def test_receipt_renders_without_breakdown_columns():
    """Old payment row, columns never set — single-line fallback."""
    pdf_bytes = build_receipt_pdf(
        payment=_base_payment(base_fee_amount=None, platform_fee_amount=None, platform_fee_percent=None, cancellation_refund_amount=None),
        appointment=APPOINTMENT,
        clinic=CLINIC,
        item_name="Initial Assessment",
    )
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_receipt_renders_itemized_breakdown_and_refund_line():
    pdf_bytes = build_receipt_pdf(
        payment=_base_payment(
            base_fee_amount=1000.0,
            platform_fee_amount=100.0,
            platform_fee_percent=10.0,
            cancellation_refund_amount=550.0,
            cancellation_refund_percent=50.0,
        ),
        appointment=APPOINTMENT,
        clinic=CLINIC,
        item_name="Initial Assessment",
    )
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_receipt_skips_platform_fee_row_when_zero():
    pdf_bytes = build_receipt_pdf(
        payment=_base_payment(base_fee_amount=1000.0, platform_fee_amount=0.0, platform_fee_percent=0.0, cancellation_refund_amount=None),
        appointment=APPOINTMENT,
        clinic=CLINIC,
        item_name="Initial Assessment",
    )
    assert pdf_bytes[:4] == b"%PDF"
