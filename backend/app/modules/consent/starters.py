"""Starter full-form HTML per consent type (Documents/design_consent_v2.md H18).

A super admin downloads or loads one of these, replaces the wording with
counsel's, and keeps the structure: every detail is an auto-filled field
(<input name="...">), guardian-only text sits in data-show-if="guardian",
[SIGNATURE_BLOCK] marks the acceptance. Each starter carries every required
and recommended field for its type (tests/unit/test_consent_starters.py).
Wording here is placeholder text, not legal text."""

from __future__ import annotations

from app.modules.consent.fill import DESCRIPTIONS

_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%%TITLE%%</title>
<style>
  @page { size: A4; margin: 18mm 16mm; }
  body { font-family: "Noto Sans", Arial, sans-serif; font-size: 13px; color: #0f172a; line-height: 1.55;
         max-width: 760px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 20px; color: #1e3a8a; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 20px 0 6px; padding-bottom: 3px; border-bottom: 1px solid #e2e8f0; }
  .muted { color: #64748b; font-size: 12px; }
  table.fields { width: 100%; border-collapse: collapse; }
  table.fields td { padding: 4px 6px; vertical-align: top; }
  table.fields td.label { width: 36%; color: #475569; }
  input, textarea { width: 100%; border: 0; border-bottom: 1px solid #94a3b8; background: transparent;
                    font: inherit; color: inherit; padding: 2px 0; }
  textarea { resize: none; }
  .box { border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px 12px; margin: 10px 0; }
  .replace { color: #b45309; }
</style>
</head>
<body>
<!-- Starter form. Replace every orange paragraph with your approved wording.
     Keep the fields: each <input name="..."> is filled automatically with the
     signer's details and locked; the patient never types into it. -->
<h1>%%TITLE%%</h1>
<p class="muted">[CLINIC_NAME] &middot; [CLINIC_ADDRESS] &middot; [CLINIC_PHONE]<br>
Form version [CONSENT_VERSION] &middot; Consent ID [CONSENT_ID]</p>

%%DETAILS%%

%%SECTIONS%%

<h2>Acceptance</h2>
<table class="fields">
  <tr><td class="label">Date of acceptance</td><td><input name="CONSENT_DATE"></td></tr>
</table>
[SIGNATURE_BLOCK]
</body>
</html>
"""


def _rows(*names: str, textareas: tuple[str, ...] = ()) -> str:
    rows = []
    for n in names:
        label = DESCRIPTIONS[n]
        field = f'<textarea name="{n}" rows="2"></textarea>' if n in textareas else f'<input name="{n}">'
        rows.append(f'  <tr><td class="label">{label}</td><td>{field}</td></tr>')
    return '<table class="fields">\n' + "\n".join(rows) + "\n</table>"


def _sections(*headings: str) -> str:
    return "\n\n".join(
        f'<h2>{i}. {h}</h2>\n<p class="replace">Replace this paragraph with the approved wording on: {h.lower()}.</p>'
        for i, h in enumerate(headings, 1)
    )


_PATIENT_DETAILS = (
    "<h2>Patient details</h2>\n"
    + _rows(
        "PATIENT_NAME",
        "PATIENT_DOB",
        "PATIENT_AGE",
        "PATIENT_GENDER",
        "PATIENT_MRN",
        "PATIENT_PHONE",
        "PATIENT_ADDRESS",
        textareas=("PATIENT_ADDRESS",),
    )
    + """

<!-- Shown only when a guardian signs (minor, or an adult who cannot consent). -->
<div class="box" data-show-if="guardian">
<h2>Guardian giving consent</h2>
"""
    + _rows("GUARDIAN_NAME", "GUARDIAN_RELATION", "GUARDIAN_PHONE", "GUARDIAN_REASON")
    + """
<p>I give this consent on behalf of the patient named above.</p>
</div>

<!-- Shown only when the patient signs for themselves. -->
<div data-show-if="self"><p>I give this consent for myself.</p></div>"""
)

_TRANSFER_DETAILS = "<h2>Transfer</h2>\n" + _rows("FROM_CLINIC", "TO_CLINIC")

_STARTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "patient_onboarding": (
        "Patient Onboarding Consent",
        (
            "Purpose of care",
            "Assessments we will carry out (EEG, rating scales, device sessions)",
            "Personal and health data we collect",
            "How we use and share your data",
            "How long we keep your records",
            "Your rights and how to withdraw consent",
            "Contact for questions or complaints",
        ),
    ),
    "patient_clinic_exit": ("Patient Clinic Exit Consent", ("Leaving the programme", "What happens to your records", "Re-joining later")),
    "patient_clinic_transfer": (
        "Patient Clinic Transfer Consent",
        ("Why the transfer is happening", "Records that move with you", "Your new doctor", "Continuing your treatment"),
    ),
    "patient_relocation_transfer": (
        "Patient Relocation Transfer Consent",
        ("Your relocation", "Records that move with you", "Your new doctor", "Continuing your treatment"),
    ),
    "staff_onboarding": (
        "Staff Onboarding Consent",
        ("Your role and responsibilities", "Patient confidentiality", "Data protection", "Your employment records"),
    ),
    "staff_offboarding": (
        "Staff Offboarding Consent",
        ("End of association", "Access removal", "Handover of patient work", "Your records"),
    ),
    "clinic_join_anava": (
        "Partner Clinic — Join Anava Network Consent",
        ("Joining the network", "Clinical protocols and standards", "Data governance", "Oversight"),
    ),
    "clinic_leave_anava": (
        "Partner Clinic — Leave Anava Network Consent",
        ("Leaving the network", "Patient records", "Continuing obligations"),
    ),
}


def starter_html(consent_type: str, role: str | None = None) -> str:
    title, headings = _STARTERS[consent_type]
    if consent_type.startswith("patient_"):
        details = _PATIENT_DETAILS + ("\n\n" + _TRANSFER_DETAILS if consent_type.endswith("_transfer") else "")
    elif consent_type.startswith("staff_"):
        if role:
            title = f"{role.replace('_', ' ').title()} {title.removeprefix('Staff ')}"
        doctor = ("DOCTOR_LICENSE_NO", "DOCTOR_SPECIALIZATION") if role == "doctor" else ()
        details = "<h2>Staff member</h2>\n" + _rows("STAFF_NAME", "STAFF_ROLE", *doctor, "STAFF_EMAIL", "STAFF_PHONE", "REGION_NAME")
    else:
        details = "<h2>Partner clinic</h2>\n" + _rows("PARTNER_CLINIC_NAME", "SIGNER_NAME")
    return _BASE.replace("%%TITLE%%", title).replace("%%DETAILS%%", details).replace("%%SECTIONS%%", _sections(*headings))
