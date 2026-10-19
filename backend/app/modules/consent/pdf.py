"""Signed-consent PDF — a printable copy of the consent_records row, which
stays the legal record. Built only from what the row stores, so a lost PDF
can be rebuilt identically in content. Noto Sans (bundled, OFL) because the
fpdf2 core fonts are Latin-1 only."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

_ASSETS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
_LOGO_PATH = os.path.join(_ASSETS, "anava_logo.png")
_IST = ZoneInfo("Asia/Kolkata")

_GUARDIAN_REASONS = {
    "minor": "Patient is under 18",
    "lacks_capacity": "Patient lacks capacity to consent",
    "lawful_guardian": "Lawful guardian",
    "nominated_representative": "Nominated representative (Mental Healthcare Act, 2017)",
}


def _fmt_time(value: datetime | None) -> str:
    return value.astimezone(_IST).strftime("%d %b %Y, %I:%M:%S %p IST") if value else "-"


def build_consent_pdf(*, record: dict, template: dict, clinic_name: str | None, subject_name: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4", unit="mm")
    pdf.add_font("Noto", "", os.path.join(_ASSETS, "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", os.path.join(_ASSETS, "NotoSans-Bold.ttf"))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(record["rendered_title"] or "Consent")
    pdf.add_page()

    if os.path.exists(_LOGO_PATH):
        pdf.image(_LOGO_PATH, x=15, y=12, w=20)
    pdf.set_xy(40, 14)
    pdf.set_font("Noto", "B", 14)
    pdf.cell(0, 7, "Anava Clinics by Mana Health Sciences", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(40)
    pdf.set_font("Noto", "", 9)
    pdf.cell(0, 5, clinic_name or "", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(8)

    pdf.set_font("Noto", "B", 13)
    pdf.multi_cell(0, 7, record["rendered_title"] or "", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Noto", "", 8)
    pdf.cell(
        0,
        5,
        f"Consent ID {record['consent_id']}  ·  {record['consent_type']}  ·  version {template['version']} ({template['language']})",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(3)

    pdf.set_font("Noto", "", 10)
    pdf.multi_cell(0, 5.5, record["rendered_content"] or "", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Noto", "B", 10)
    pdf.cell(0, 6, "Responses", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Noto", "", 10)
    for r in record["responses"] or []:
        mark = "[X]" if r["accepted"] else "[  ]"
        suffix = "" if r["required"] else " (optional)"
        pdf.multi_cell(0, 5.5, f"{mark}  {r['label']}{suffix}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Noto", "B", 10)
    pdf.cell(0, 6, "Signature record", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def row(label: str, value) -> None:
        pdf.set_font("Noto", "B", 9)
        pdf.cell(45, 5.5, label)
        pdf.set_font("Noto", "", 9)
        pdf.multi_cell(0, 5.5, str(value) if value else "-", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    guardian = record["signer_type"] == "guardian"
    row("Consent for", subject_name)
    row("Signed by", record["signer_name"])
    row("Signer", "Guardian" if guardian else "Self")
    if guardian:
        row("Relation to patient", record["signer_relation"])
        row("Reason", _GUARDIAN_REASONS.get(record["guardian_reason"] or "", record["guardian_reason"]))
    row("Signer contact", record["signer_contact"])
    row("Signed at", _fmt_time(record["signed_at"]))
    row("Method", "Electronic acceptance in the signer's own authenticated session")
    row("Authentication", record["auth_method"])
    row("IP address", record["ip_address"])
    row("Device", record["user_agent"])
    row("Text hash (SHA-256)", record["rendered_hash"])
    row("Template hash", record["content_hash_at_signing"])

    pdf.ln(4)
    pdf.set_font("Noto", "", 7.5)
    pdf.multi_cell(
        0,
        4,
        "This document is a copy of an electronic record of consent held by Anava Clinics. "
        "The text hash identifies the exact wording and checkbox labels shown to the signer.",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    return bytes(pdf.output())
