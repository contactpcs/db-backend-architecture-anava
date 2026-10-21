"""Consent v3 H18: the per-type field rules, the upload analysis the super
admin sees before saving, and the starter forms Anava hands them."""

import pytest

from app.modules.consent.fill import analyze_full_html, check_full_html, recommended_placeholders, required_placeholders
from app.modules.consent.starters import _STARTERS, starter_html

CASES = [(t, r) for t in _STARTERS for r in ((None, "doctor", "receptionist") if t.startswith("staff_") else (None,))]


@pytest.mark.parametrize(("consent_type", "role"), CASES)
def test_every_starter_is_safe_complete_and_only_uses_known_fields(consent_type, role):
    html = starter_html(consent_type, role)
    a = analyze_full_html(consent_type, role, html)
    assert check_full_html(html) == []
    assert a["missing_required"] == [] and a["missing_recommended"] == [] and a["unknown"] == []
    assert html.startswith("<!DOCTYPE html>")


def test_starters_capture_details_as_locked_input_fields():
    a = analyze_full_html("patient_onboarding", None, starter_html("patient_onboarding"))
    by_name = {f["name"]: f for f in a["fields"]}
    assert by_name["PATIENT_NAME"]["as_input"] and by_name["PATIENT_DOB"]["required"]
    assert by_name["SIGNATURE_BLOCK"]["as_text"] and not by_name["SIGNATURE_BLOCK"]["as_input"]
    assert a["has_guardian_section"]


def test_required_fields_per_type():
    assert required_placeholders("patient_onboarding") == (
        "PATIENT_NAME",
        "PATIENT_DOB",
        "GUARDIAN_NAME",
        "CONSENT_DATE",
        "SIGNATURE_BLOCK",
    )
    assert {"FROM_CLINIC", "TO_CLINIC"} <= set(required_placeholders("patient_clinic_transfer"))
    assert "DOCTOR_LICENSE_NO" in required_placeholders("staff_onboarding", "doctor")
    assert "DOCTOR_LICENSE_NO" not in required_placeholders("staff_onboarding", "receptionist")
    assert required_placeholders("clinic_join_anava")[:2] == ("PARTNER_CLINIC_NAME", "SIGNER_NAME")
    assert "PATIENT_MRN" in recommended_placeholders("patient_onboarding")


def test_analysis_lists_fields_and_what_is_missing():
    html = """<p>Name <input name="patient_name"> born [PATIENT_DOB]</p>
    <!-- <input name="PATIENT_MRN"> is only in a comment -->
    <p>Job <input name="occupation"></p><script>x()</script>"""
    a = analyze_full_html("patient_onboarding", None, html)
    assert [(f["name"], f["as_input"], f["as_text"], f["known"]) for f in a["fields"]] == [
        ("OCCUPATION", True, False, False),
        ("PATIENT_DOB", False, True, True),
        ("PATIENT_NAME", True, False, True),
    ]
    assert a["missing_required"] == ["GUARDIAN_NAME", "CONSENT_DATE", "SIGNATURE_BLOCK"]
    assert "PATIENT_MRN" in a["missing_recommended"]  # the commented-out field does not count
    assert a["unknown"] == ["OCCUPATION"] and a["problems"] == ["<script> is not allowed"]
    assert not a["has_guardian_section"]


def test_role_placeholder_counts_as_staff_role():
    a = analyze_full_html("staff_offboarding", None, "<p>[STAFF_NAME] as [ROLE] on [CONSENT_DATE]</p>[SIGNATURE_BLOCK]")
    assert a["missing_required"] == []
