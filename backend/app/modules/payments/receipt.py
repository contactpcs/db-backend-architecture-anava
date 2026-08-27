"""Branded PDF receipt — built fresh from DB data on every request, nothing
stored on disk. fpdf2 imported lazily (matches integrations/razorpay.py's
lazy `import razorpay`) since it's only needed on this one code path."""

from __future__ import annotations

import os
from decimal import Decimal

_LOGO_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "anava_logo.png"))
_CLINIC_TITLE = "Anava Clinics By Mana Health Sciences"


def _fmt_amount(amount, currency: str) -> str:
    return f"{currency} {Decimal(str(amount)):.2f}"


def _fmt_payment_method(payment_method) -> str:
    if not payment_method:
        return "-"
    # Internal path-tracking values (razorpay_webhook / razorpay_checkout,
    # see payments/service.py) collapse to one label on a customer-facing
    # document — which of our two confirmation paths caught it isn't the
    # patient's concern.
    if str(payment_method).startswith("razorpay"):
        return "Razorpay (Online)"
    return str(payment_method).replace("_", " ").title()


def build_receipt_pdf(*, payment: dict, appointment: dict, clinic: dict, item_name: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    if os.path.exists(_LOGO_PATH):
        pdf.image(_LOGO_PATH, x=15, y=12, w=22)

    pdf.set_xy(42, 14)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 8, _CLINIC_TITLE, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 10)
    branch = clinic.get("clinic_name") or "Anava Clinic"
    address = ", ".join(b for b in (clinic.get("address"), clinic.get("city"), clinic.get("state")) if b)
    pdf.set_x(42)
    pdf.cell(0, 5, branch, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if address:
        pdf.set_x(42)
        pdf.cell(0, 5, address, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if clinic.get("phone"):
        pdf.set_x(42)
        pdf.cell(0, 5, f"Phone: {clinic['phone']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "PAYMENT RECEIPT", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    pdf.set_font("Helvetica", "", 10)
    receipt_no = f"RCPT-{str(payment['payment_id']).replace('-', '')[:10].upper()}"
    paid_at = payment.get("paid_at")
    pdf.cell(0, 6, f"Receipt No: {receipt_no}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f"Date: {paid_at.strftime('%d %b %Y, %I:%M %p') if paid_at else '-'}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    def row(label: str, value) -> None:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(50, 7, label)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, str(value) if value else "-", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    row("Patient", appointment.get("patient_name"))
    if appointment.get("doctor_name"):
        row("Doctor", appointment.get("doctor_name"))
    row("Appointment Type", str(appointment.get("appointment_type") or "").replace("_", " ").title())
    appt_date, appt_time = appointment.get("appointment_date"), appointment.get("start_time")
    date_str = appt_date.strftime("%d %b %Y") if appt_date else ""
    time_str = appt_time.strftime("%I:%M %p") if appt_time else ""
    row("Date & Time", f"{date_str} {time_str}".strip())

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 8, "Description", border=1)
    pdf.cell(0, 8, "Amount", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
    pdf.set_font("Helvetica", "", 10)

    currency = payment["currency"]
    base_fee_amount = payment.get("base_fee_amount")
    platform_fee_amount = payment.get("platform_fee_amount")
    platform_fee_percent = payment.get("platform_fee_percent")

    # base_fee_amount is only ever NULL for a payment created before the fee-
    # breakdown columns existed (64_fee_breakdown_and_cancellation_policy.sql)
    # — fall back to the single line the receipt always used to show.
    if base_fee_amount is not None:
        pdf.cell(90, 8, item_name, border=1)
        pdf.cell(0, 8, _fmt_amount(base_fee_amount, currency), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        if platform_fee_amount and Decimal(str(platform_fee_amount)) > 0:
            label = "Platform & Convenience Fee"
            if platform_fee_percent is not None:
                label += f" ({Decimal(str(platform_fee_percent)):.2f}%)"
            pdf.cell(90, 8, label, border=1)
            pdf.cell(0, 8, _fmt_amount(platform_fee_amount, currency), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(90, 8, "Total", border=1)
        pdf.cell(0, 8, _fmt_amount(payment["amount"], currency), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
    else:
        pdf.cell(90, 8, item_name, border=1)
        pdf.cell(0, 8, _fmt_amount(payment["amount"], currency), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
    pdf.ln(4)

    row("Payment Method", _fmt_payment_method(payment.get("payment_method")))
    row("Razorpay Payment ID", payment.get("razorpay_payment_id"))
    row("Status", str(payment.get("status") or "").title())

    cancellation_refund_amount = payment.get("cancellation_refund_amount")
    if cancellation_refund_amount is not None:
        refund_percent = payment.get("cancellation_refund_percent")
        row(
            "Cancellation Refund Due",
            f"{_fmt_amount(cancellation_refund_amount, currency)}" + (f" ({Decimal(str(refund_percent)):.0f}%)" if refund_percent is not None else ""),
        )

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, "This is a computer-generated receipt and does not require a signature.")

    return bytes(pdf.output())
