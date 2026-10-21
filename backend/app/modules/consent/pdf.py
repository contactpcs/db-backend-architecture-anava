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
    "patient_requested": "Patient asked for a guardian's help",
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


# ─── Consent v3: the full form (H5, H10) ─────────────────────────────────


def _evidence_html(*, record: dict, template: dict, clinic_name: str | None, subject_name: str) -> str:
    from html import escape

    def cell(value) -> str:
        return escape(str(value)) if value not in (None, "") else "-"

    guardian = record["signer_type"] == "guardian"
    rows = [
        ("Consent for", subject_name),
        ("Signed by", record["signer_name"]),
        ("Signer", "Guardian" if guardian else "Self"),
    ]
    if guardian:
        rows += [
            ("Relation to patient", record.get("signer_relation")),
            ("Reason", _GUARDIAN_REASONS.get(record.get("guardian_reason") or "", record.get("guardian_reason"))),
        ]
    rows += [
        ("Signer contact", record.get("signer_contact")),
        ("Signed at", _fmt_time(record["signed_at"])),
        ("Method", "Electronic acceptance (clickwrap) in the signer's own authenticated session"),
        ("Full form opened", _fmt_time(record["full_viewed_at"]) if record.get("full_viewed_at") else "Not opened"),
        ("Authentication", record.get("auth_method")),
        ("IP address", record.get("ip_address")),
        ("Device", record.get("user_agent")),
        ("Text hash (SHA-256)", record.get("rendered_hash")),
        ("Full form hash", record.get("rendered_full_hash")),
        ("Template hash", record.get("content_hash_at_signing")),
    ]
    responses = "".join(
        f"<li><span class='box'>{'&#9745;' if r['accepted'] else '&#9744;'}</span> {escape(r['label'])}"
        f"{'' if r['required'] else ' <em>(optional)</em>'}</li>"
        for r in record.get("responses") or []
    )
    table = "".join(f"<tr><th>{escape(k)}</th><td>{cell(v)}</td></tr>" for k, v in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 16mm 15mm; }}
body {{ font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 10pt; color: #0f172a; }}
h1 {{ font-size: 14pt; margin: 0 0 2mm; }} h2 {{ font-size: 11pt; margin: 6mm 0 2mm; }}
.meta {{ font-size: 8pt; color: #475569; }} .summary {{ white-space: pre-wrap; border: 1px solid #cbd5e1; padding: 3mm; }}
ul {{ list-style: none; padding: 0; }} li {{ margin: 1.5mm 0; }} .box {{ font-size: 12pt; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ text-align: left; vertical-align: top; padding: 1.2mm 2mm;
  border-bottom: 1px solid #e2e8f0; font-size: 9pt; word-break: break-all; }} th {{ width: 45mm; word-break: normal; }}
.foot {{ margin-top: 6mm; font-size: 7.5pt; color: #475569; }}
</style></head><body>
<h1>Consent acceptance record</h1>
<div class="meta">Anava Clinics by Mana Health Sciences · {cell(clinic_name)} · Consent ID {cell(record["consent_id"])} ·
{cell(record["consent_type"])} · version {cell(template["version"])} ({cell(template["language"])})</div>
<h2>{cell(record.get("rendered_title"))} — summary shown to the signer</h2>
<div class="summary">{cell(record.get("rendered_content"))}</div>
<h2>Responses</h2><ul>{responses}</ul>
<h2>Signature record</h2><table>{table}</table>
<div class="foot">This document is a copy of an electronic record of consent held by Anava Clinics. The pages before this
one are the full consent form exactly as accepted. The hashes identify the exact wording shown to the signer.</div>
</body></html>"""


def build_full_consent_pdf(*, full_html: str, record: dict, template: dict, clinic_name: str | None, subject_name: str) -> bytes:
    """The filled full form as uploaded, then the evidence page. Network is
    blocked: only data: URIs load (H4/H5 — no SSRF, no tracking pixels)."""
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    fetcher = URLFetcher(allowed_protocols={"data"})
    form = HTML(string=full_html, url_fetcher=fetcher).render()
    evidence = HTML(
        string=_evidence_html(record=record, template=template, clinic_name=clinic_name, subject_name=subject_name), url_fetcher=fetcher
    ).render()
    doc = form.copy(form.pages + evidence.pages)
    doc.metadata.title = record.get("rendered_title") or "Consent"
    return doc.write_pdf()
