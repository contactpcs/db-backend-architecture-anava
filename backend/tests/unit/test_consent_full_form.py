"""Consent v3 (Documents/design_consent_v2.md H1-H11): the full HTML form
behind the summary. Written as the people who use it — a super admin
uploading forms (some careless, some hostile), a patient or guardian reading
and accepting, a staff member onboarding."""

import uuid
from datetime import UTC, date, datetime
from ipaddress import IPv4Address

import pytest

from app.core.exceptions import ValidationError
from app.modules.consent import fill
from app.modules.consent.fill import check_full_html, check_placeholders, hash_rendered, render, signature_block, used_values
from app.modules.consent.pdf import _evidence_html
from app.modules.consent.service import build_values, evidence_of, resolve_signer, time_values, validate_template

TODAY = date(2026, 10, 21)
SIGNED_AT = datetime(2026, 10, 21, 5, 30, tzinfo=UTC)  # 11:00 IST

FULL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Consent</title>
<style>body{font-family:serif} .x{background:url(data:image/png;base64,AAAA)}</style></head>
<body>
<h1>Patient consent — [CLINIC_NAME]</h1>
<p>Name: [PATIENT_NAME], DOB [PATIENT_DOB], age [PATIENT_AGE], MRN [PATIENT_MRN]</p>
<div data-show-if="guardian"><p>Guardian: [GUARDIAN_NAME] ([GUARDIAN_RELATION]) — [GUARDIAN_REASON]</p></div>
<div data-show-if="self"><p>I consent for myself.</p></div>
<p>Date: [CONSENT_DATE]</p>
[SIGNATURE_BLOCK]
<p>See <a href="https://www.meity.gov.in/">DPDP Act</a>.</p>
</body></html>"""

TEMPLATE = {
    "consent_type": "patient_onboarding",
    "version": 3,
    "language": "en",
    "title": "Consent for [PATIENT_NAME]",
    "content": "Summary: you agree to care at [CLINIC_NAME].",
    "clauses": [{"code": "agree", "label": "I have read the full consent form and agree", "required": True}],
    "full_html": FULL,
}


def _subject(**kw):
    return {
        "role": "patient",
        "first_name": "Asha",
        "last_name": "Rao",
        "dob": date(1990, 1, 1),
        "gender": "female",
        "phone": "+919876543210",
        "email": "pending-1@no-email.local",
        "address": "12 MG Road",
        "city": "Bengaluru",
        "state": None,
        "pincode": "560001",
        "mrn": "ANV-1",
        "guardian_name": None,
        "guardian_relationship": None,
        "guardian_contact": None,
        "guardian_reason": None,
        **kw,
    }


def _values(subject=None, **kw):
    subject = subject or _subject()
    signer = resolve_signer(subject, TODAY)
    values = build_values(
        subject=subject,
        clinic={"clinic_name": "Anava Indiranagar", "address": "100 Ft Rd", "city": "Bengaluru"},
        signer=signer,
        record={"consent_id": uuid.UUID(int=7)},
        version=3,
        today=TODAY,
        **kw,
    )
    return values, signer


# ── Super admin uploading the full form ─────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "<p>hi</p><script>alert(1)</script>",
        "<SCRIPT SRC=x></SCRIPT>",
        '<img src="data:image/png;base64,AA" onerror="alert(1)">',
        '<a href="javascript:alert(1)">x</a>',
        '<a href="&#106;avascript:alert(1)">entity-encoded</a>',
        '<a href="java\tscript:alert(1)">tab inside scheme</a>',
        '<img src="https://tracker.example.com/p.gif">',
        '<img src="//cdn.example.com/logo.png">',
        '<img srcset="https://x/a.png 2x">',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">',
        "<style>@import url('https://x/y.css');</style>",
        "<style>@font-face{font-family:X;src:url(https://x/font.woff2)}</style>",
        '<div style="background:url(http://x/bg.png)">x</div>',
        '<iframe src="data:text/html,hi"></iframe>',
        '<form action="https://evil"><input name="otp"></form>',
        '<input type="checkbox"> I agree',
        '<meta http-equiv="refresh" content="0;url=https://evil">',
        '<svg><use href="https://x/sprite.svg#a"/></svg>',
        '<object data="https://x/a.swf"></object>',
        '<embed src="data:x">',
        '<base href="https://evil/">',
        '<div style="width:expression(alert(1))">ie</div>',
    ],
)
def test_unsafe_full_form_is_rejected_not_cleaned(bad):
    assert check_full_html(bad), bad


@pytest.mark.parametrize(
    "ok",
    [
        FULL,
        '<img src="data:image/png;base64,iVBORw0KGgo=" alt="logo">',
        '<a href="https://www.meity.gov.in/">link out</a> <a href="mailto:dpo@anava.in">mail</a> <a href="#s2">jump</a>',
        "<style>@font-face{font-family:Brand;src:url(data:font/woff2;base64,AAAA)}</style>",
        '<p class="MsoNormal"><o:p></o:p>Exported from Word</p>',
        "<details><summary>Section 4</summary><p>Collapsible without JS</p></details>",
        '<svg viewBox="0 0 10 10"><use href="#dot"/></svg>',
        "<p>Patients onboard at [CLINIC_NAME] (ref. one) — once</p>",  # 'on' in text is not an attribute
    ],
)
def test_safe_full_form_is_accepted(ok):
    assert check_full_html(ok) == []


def test_oversized_full_form_is_rejected():
    big = "<p>" + "x" * (fill.FULL_HTML_MAX_BYTES + 1) + "</p>"
    assert "larger than" in check_full_html(big)[0]


def test_multibyte_text_counts_bytes_not_characters():
    telugu = "<p>" + "అ" * 400_000 + "</p>"  # 400k chars, 1.2 MB in UTF-8
    assert check_full_html(telugu)


def test_placeholder_typos_and_wrong_type_are_reported():
    assert check_placeholders("patient_onboarding", "[PATIENT_NAM]", "[STAFF_NAME]", "[CLINIC_NAME]") == ["PATIENT_NAM", "STAFF_NAME"]
    assert check_placeholders("staff_onboarding", "[ROLE] at [CLINIC_NAME]", "[PATIENT_MRN]") == ["PATIENT_MRN"]
    assert check_placeholders("patient_onboarding", "[TO_CLINIC]") == ["TO_CLINIC"]
    assert check_placeholders("patient_clinic_transfer", "[FROM_CLINIC] to [TO_CLINIC]") == []
    assert check_placeholders("clinic_join_anava", "[PARTNER_CLINIC_NAME]") == []


def test_footnotes_and_lowercase_brackets_are_not_placeholders():
    assert check_placeholders("patient_onboarding", "See note [1], [a], [Ok] and [AB]") == []


def test_validate_template_names_every_problem():
    with pytest.raises(ValidationError) as exc:
        validate_template("patient_onboarding", title="t", content="[SIGNATURE_BLOCK]", clauses=[], full_html=None)
    assert exc.value.code == "CONSENT_TEMPLATE_PLACEHOLDER"
    with pytest.raises(ValidationError) as exc:
        validate_template("patient_onboarding", title="t", content="c", clauses=[{"code": "a", "label": "[STAFF_ROLE]"}], full_html=None)
    assert exc.value.code == "CONSENT_TEMPLATE_PLACEHOLDER"
    with pytest.raises(ValidationError) as exc:
        validate_template("patient_onboarding", title="t", content="c", clauses=[], full_html="<script></script><iframe></iframe>")
    assert exc.value.code == "CONSENT_TEMPLATE_UNSAFE_HTML"
    assert len(exc.value.details[0]["problems"]) == 2
    validate_template("patient_onboarding", title="t", content="c", clauses=[], full_html=FULL)


# ── Patient reading and accepting ───────────────────────────────────────


def test_full_form_is_filled_and_the_uploaded_markup_is_kept():
    values, signer = _values()
    out = render(TEMPLATE, values, signer["signer_type"], times=time_values(None, TODAY), signature=signature_block(None))
    html = out["full_html"]
    assert html.startswith("<!DOCTYPE html>")  # nothing injected before the doctype (quirks mode)
    assert '<head><base target="_blank"><style>[data-show-if="guardian"]{display:none!important}</style>' in html
    assert "Name: Asha Rao, DOB 01 Jan 1990, age 36, MRN ANV-1" in html
    assert "Date: 21 Oct 2026" in html
    assert "recorded here when you press" in html
    # The rest of the uploaded document is byte-for-byte the same.
    assert '<p>See <a href="https://www.meity.gov.in/">DPDP Act</a>.</p>' in html
    assert ".x{background:url(data:image/png;base64,AAAA)}" in html


def test_names_with_html_characters_are_escaped_in_the_form_but_plain_in_the_summary():
    values, signer = _values(_subject(first_name="<b>Asha</b>", last_name="O'Brien & Co"))
    out = render(TEMPLATE, values, signer["signer_type"], times=None)
    assert "&lt;b&gt;Asha&lt;/b&gt; O&#x27;Brien &amp; Co" in out["full_html"]
    assert "<b>Asha</b>" not in out["full_html"]
    assert out["title"] == "Consent for <b>Asha</b> O'Brien & Co"  # React escapes it on screen


def test_guardian_section_shown_only_when_a_guardian_signs():
    minor = _subject(dob=date(2015, 5, 1), guardian_name="Ravi Rao", guardian_relationship="father", guardian_contact="+919876500000")
    values, signer = _values(minor)
    html = render(TEMPLATE, values, signer["signer_type"], times=None)["full_html"]
    assert '[data-show-if="self"]{display:none!important}' in html
    assert "Guardian: Ravi Rao (Father) — Patient is under 18" in html
    own = render(TEMPLATE, _values()[0], "self", times=None)["full_html"]
    assert '[data-show-if="guardian"]{display:none!important}' in own
    assert "Guardian: — (—) — —" in own  # filled with blanks, and hidden by the style


def test_style_goes_after_html_or_doctype_when_there_is_no_head():
    t = {**TEMPLATE, "full_html": "<!doctype html><p>[PATIENT_NAME]</p>"}
    assert render(t, _values()[0], "self", times=None)["full_html"].startswith('<!doctype html><base target="_blank"><style>')
    t = {**TEMPLATE, "full_html": "<p>[PATIENT_NAME]</p>"}
    assert render(t, _values()[0], "self", times=None)["full_html"].startswith('<base target="_blank"><style>')


def test_missing_details_show_a_dash_not_a_raw_placeholder():
    values, signer = _values(_subject(dob=None, mrn=None, address=None, city=None, pincode=None))
    t = {**TEMPLATE, "content": "DOB [PATIENT_DOB], address [PATIENT_ADDRESS], email [PATIENT_EMAIL]"}
    assert render(t, values, signer["signer_type"], times=None)["content"] == "DOB —, address —, email —"


def test_hash_ignores_the_clock_so_a_form_left_open_overnight_still_signs():
    values, signer = _values()
    canonical = render(TEMPLATE, values, "self", times=None)
    assert "[CONSENT_DATE]" in canonical["full_html"] and "[SIGNATURE_BLOCK]" in canonical["full_html"]
    tomorrow = time_values(None, date(2026, 10, 22))
    shown = render(TEMPLATE, values, "self", times=tomorrow, signature=signature_block(None))
    assert hash_rendered(canonical) == hash_rendered(render(TEMPLATE, values, "self", times=None))
    assert "Date: 22 Oct 2026" in shown["full_html"]


def test_hash_changes_when_the_admin_edits_only_the_full_form():
    values, _ = _values()
    a = hash_rendered(render(TEMPLATE, values, "self", times=None))
    b = hash_rendered(render({**TEMPLATE, "full_html": FULL.replace("I consent", "I agree")}, values, "self", times=None))
    assert a != b


def test_signed_form_rebuilds_identically_from_the_minimal_snapshot():
    """H9: the PDF/full form years later comes from fill_values only."""
    values, signer = _values()
    record = {
        "consent_id": uuid.UUID(int=7),
        "signer_type": "self",
        "signer_name": "Asha Rao",
        "signed_at": SIGNED_AT,
        "ip_address": IPv4Address("203.0.113.5"),
        "rendered_hash": "ab" * 32,
        "fill_values": used_values(TEMPLATE, values),
    }
    times = time_values(SIGNED_AT, TODAY)
    at_signing = render(
        TEMPLATE, values, "self", times=times, signature=signature_block(evidence_of({**record, "ip_address": "203.0.113.5"}))
    )
    later = render(TEMPLATE, record["fill_values"], "self", times=times, signature=signature_block(evidence_of(record)))
    assert at_signing["full_html"] == later["full_html"]
    assert "Accepted electronically by Asha Rao" in later["full_html"]
    assert "21 Oct 2026, 11:00:00 AM IST" in later["full_html"]


def test_snapshot_keeps_only_what_the_form_shows():
    values, _ = _values()
    snap = used_values(TEMPLATE, values)
    assert "PATIENT_PHONE" not in snap and "PATIENT_ADDRESS" not in snap  # not on this form
    assert snap["PATIENT_MRN"] == "ANV-1" and "CONSENT_DATE" not in snap and "SIGNATURE_BLOCK" not in snap


def test_signature_block_names_who_a_guardian_signs_for_and_escapes():
    block = signature_block(
        {
            "signer_type": "guardian",
            "name": "Ravi <Rao>",
            "relation": "father",
            "on_behalf_of": "Kiran",
            "signed_at": "21 Oct 2026",
            "ip_address": None,
            "consent_id": "c1",
            "rendered_hash": "h",
        }
    )
    assert "Ravi &lt;Rao&gt;" in block and "on behalf of Kiran" in block and "IP address —" in block


# ── Staff and transfers ─────────────────────────────────────────────────


def test_doctor_form_gets_licence_role_and_region():
    doctor = _subject(role="doctor", first_name="Meera", last_name="Iyer", license_number="KMC-1", specialization="Neurology")
    values, signer = _values(doctor, region_name="Karnataka")
    t = {
        **TEMPLATE,
        "consent_type": "staff_onboarding",
        "content": "[STAFF_NAME] joins [CLINIC_NAME] as [ROLE] ([DOCTOR_LICENSE_NO], [DOCTOR_SPECIALIZATION]) in [REGION_NAME]",
    }
    assert render(t, values, signer["signer_type"], times=None)["content"] == (
        "Meera Iyer joins Anava Indiranagar as Doctor (KMC-1, Neurology) in Karnataka"
    )
    assert check_placeholders("staff_onboarding", t["content"]) == []


def test_receptionist_form_shows_dash_for_doctor_only_fields():
    values, _ = _values(_subject(role="clinical_assistant"))
    assert values["STAFF_ROLE"] == "Clinical Assistant" and values["DOCTOR_LICENSE_NO"] == ""


def test_transfer_form_names_both_clinics():
    values, _ = _values(transfer={"from_clinic": "Anava Indiranagar", "to_clinic": "Anava Jubilee Hills"})
    t = {**TEMPLATE, "consent_type": "patient_clinic_transfer", "content": "From [FROM_CLINIC] to [TO_CLINIC]"}
    assert render(t, values, "self", times=None)["content"] == "From Anava Indiranagar to Anava Jubilee Hills"


# ── PDF evidence page ───────────────────────────────────────────────────


def _signed_record():
    return {
        "consent_id": uuid.uuid4(),
        "consent_type": "patient_onboarding",
        "rendered_title": "Consent for <Asha>",
        "rendered_content": "Summary & terms",
        "responses": [{"code": "agree", "label": "I agree <now>", "required": True, "accepted": True}],
        "signer_type": "guardian",
        "signer_name": "Ravi",
        "signer_relation": "father",
        "signer_contact": "+919876543210",
        "guardian_reason": "minor",
        "signed_at": SIGNED_AT,
        "full_viewed_at": None,
        "auth_method": "cognito_password_session",
        "ip_address": IPv4Address("1.2.3.4"),
        "user_agent": "Mozilla/5.0 <x>",
        "rendered_hash": "ab" * 32,
        "rendered_full_hash": "cd" * 32,
        "content_hash_at_signing": "ef" * 32,
    }


def test_evidence_page_escapes_and_records_everything():
    page = _evidence_html(record=_signed_record(), template={"version": 3, "language": "en"}, clinic_name="A&B", subject_name="Kiran")
    assert "Consent for &lt;Asha&gt;" in page and "I agree &lt;now&gt;" in page and "Mozilla/5.0 &lt;x&gt;" in page
    assert "Patient is under 18" in page and "Not opened" in page and "cd" * 32 in page and "A&amp;B" in page


def test_full_pdf_renders_the_form_then_the_evidence_page():
    pytest.importorskip("weasyprint", exc_type=OSError)  # needs Pango (Linux image)
    from app.modules.consent.pdf import build_full_consent_pdf

    values, _ = _values()
    html = render(TEMPLATE, values, "self", times=time_values(SIGNED_AT, TODAY), signature=signature_block(None))["full_html"]
    html = html.replace("</body>", '<img src="https://tracker.example.com/p.gif"></body>')  # must not be fetched
    pdf = build_full_consent_pdf(
        full_html=html, record=_signed_record(), template={"version": 3, "language": "en"}, clinic_name="C", subject_name="Asha"
    )
    assert pdf.startswith(b"%PDF")
    pypdf = pytest.importorskip("pypdf")
    import io

    pages = [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]
    assert len(pages) >= 2
    assert "Name: Asha Rao" in pages[0] and "MRN ANV-1" in pages[0]
    assert "Guardian:" not in pages[0]  # data-show-if="guardian" hidden for a self-signer
    assert "Consent acceptance record" in pages[-1] and "Patient is under 18" in pages[-1]


# ── Auto-filled input fields in the full form (H17) ─────────────────────

FORM_WITH_FIELDS = """<!doctype html><html><head><style>input{border:0;border-bottom:1px solid #000}</style></head><body>
<p>Name: <input name="PATIENT_NAME" placeholder="Full name" value="typed by admin"></p>
<p>DOB: <input type="date" name='patient_dob' readonly disabled/></p>
<p>Age: <input name=PATIENT_AGE size=4></p>
<p>Date: <input name="CONSENT_DATE"></p>
<p>Address:<br><textarea name="PATIENT_ADDRESS" rows="2">old text</textarea></p>
[SIGNATURE_BLOCK]
</body></html>"""


def test_input_fields_are_filled_and_locked():
    values, _ = _values(_subject(first_name='Asha "AJ"'))
    t = {**TEMPLATE, "full_html": FORM_WITH_FIELDS}
    html = render(t, values, "self", times=time_values(SIGNED_AT, TODAY), signature=signature_block(None))["full_html"]
    assert '<input name="PATIENT_NAME" placeholder="Full name" type="text" value="Asha &quot;AJ&quot; Rao" readonly>' in html
    assert "typed by admin" not in html  # the admin's own value never reaches the signer
    assert '<input name=\'patient_dob\' type="text" value="01 Jan 1990" readonly>' in html  # date type would blank it
    assert 'value="36" readonly' in html and 'value="21 Oct 2026" readonly' in html
    assert '<textarea name="PATIENT_ADDRESS" rows="2" readonly>12 MG Road, Bengaluru, 560001</textarea>' in html
    assert "old text" not in html and "disabled" not in html


def test_input_fields_count_as_placeholders_for_checks_hash_and_snapshot():
    t = {**TEMPLATE, "full_html": FORM_WITH_FIELDS}
    assert check_placeholders("patient_onboarding", FORM_WITH_FIELDS) == []
    assert check_placeholders("staff_onboarding", FORM_WITH_FIELDS) == ["PATIENT_ADDRESS", "PATIENT_AGE", "PATIENT_DOB", "PATIENT_NAME"]
    assert check_placeholders("patient_onboarding", '<input name="occupation">') == ["OCCUPATION"]
    values, _ = _values()
    canonical = render(t, values, "self", times=None)["full_html"]
    assert 'value="[CONSENT_DATE]"' in canonical  # time field stays a token while hashing
    assert {"PATIENT_DOB", "PATIENT_AGE", "PATIENT_ADDRESS"} <= set(used_values(t, values))


@pytest.mark.parametrize(
    "bad",
    [
        "<input>",
        '<input name="">',
        '<input name="x">',
        '<input type="checkbox" name="PATIENT_NAME">',
        '<input type="hidden" name="PATIENT_MRN">',
        '<input type="submit" name="PATIENT_NAME">',
        "<textarea>free text</textarea>",
        '<input name="SIGNATURE_BLOCK">',
    ],
)
def test_input_fields_must_be_named_text_fields(bad):
    assert check_full_html(bad), bad


def test_named_text_inputs_are_safe():
    assert check_full_html(FORM_WITH_FIELDS) == []


# ── Summary page shows no personal details (H16) ────────────────────────


def test_personal_placeholders_only_in_the_full_form():
    with pytest.raises(ValidationError) as exc:
        validate_template("patient_onboarding", title="Consent for [PATIENT_NAME]", content="c", clauses=[], full_html=None)
    assert exc.value.code == "CONSENT_TEMPLATE_PERSONAL_IN_SUMMARY"
    for field in ("content", "clauses"):
        kw = {"title": "t", "content": "c", "clauses": [], "full_html": None}
        kw[field] = "I, [SIGNER_NAME], agree" if field == "content" else [{"code": "ab", "label": "[GUARDIAN_NAME] agrees"}]
        with pytest.raises(ValidationError):
            validate_template("patient_onboarding", **kw)
    with pytest.raises(ValidationError):
        validate_template("staff_onboarding", title="t", content="[STAFF_NAME] joins", clauses=[], full_html=None)
    # Clinic, role, version and dates are not personal; personal fields are fine in the full form.
    validate_template(
        "staff_onboarding",
        title="t",
        content="Joining [CLINIC_NAME] as [ROLE], version [CONSENT_VERSION]",
        clauses=[],
        full_html="<p>[STAFF_NAME] <input name=STAFF_EMAIL></p>",
    )
    validate_template(
        "patient_onboarding", title="t", content="Care at [CLINIC_NAME] from [CONSENT_DATE]", clauses=[], full_html=FORM_WITH_FIELDS
    )


def test_full_pdf_prints_the_filled_input_fields():
    pytest.importorskip("weasyprint", exc_type=OSError)
    pypdf = pytest.importorskip("pypdf")
    import io

    from app.modules.consent.pdf import build_full_consent_pdf

    values, _ = _values()
    html = render(
        {**TEMPLATE, "full_html": FORM_WITH_FIELDS}, values, "self", times=time_values(SIGNED_AT, TODAY), signature=signature_block(None)
    )["full_html"]
    pdf = build_full_consent_pdf(
        full_html=html, record=_signed_record(), template={"version": 3, "language": "en"}, clinic_name="C", subject_name="Asha"
    )
    first = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Asha Rao" in first and "01 Jan 1990" in first and "12 MG Road" in first
