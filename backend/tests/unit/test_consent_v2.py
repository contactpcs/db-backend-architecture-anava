"""Consent v2 rules (Documents/design_consent_v2.md): signer resolution,
rendering + hash, checkbox validation, guardian login rules, PDF and key
shapes."""

import re
import uuid
from datetime import UTC, date, datetime
from ipaddress import IPv4Address

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import BusinessRuleError, ValidationError
from app.integrations import s3
from app.modules.auth.signup import check_guardian_login
from app.modules.consent.fill import hash_rendered, render
from app.modules.consent.pdf import build_consent_pdf
from app.modules.consent.schemas import ConsentTemplateCreate
from app.modules.consent.service import build_responses, build_values, resolve_signer

TODAY = date(2026, 10, 16)


def _patient(**kw):
    return {
        "role": "patient",
        "first_name": "Asha",
        "last_name": "Rao",
        "dob": date(1990, 1, 1),
        "phone": "+919876543210",
        "email": "pending-x@no-email.local",
        "guardian_name": None,
        "guardian_relationship": None,
        "guardian_contact": None,
        "guardian_reason": None,
        **kw,
    }


TEMPLATE = {
    "title": "Consent for [PATIENT_NAME]",
    "content": "I, [SIGNER_NAME], agree to care at [CLINIC_NAME].",
    "clauses": [
        {"code": "data", "label": "Process data of [PATIENT_NAME]", "required": True},
        {"code": "research", "label": "Use for research", "required": False},
    ],
}


# ── signer ────────────────────────────────────────────────────────────────


def test_adult_signs_for_themselves_and_placeholder_email_is_not_a_contact():
    signer = resolve_signer(_patient(phone=None), TODAY)
    assert signer == {"signer_type": "self", "name": "Asha Rao", "contact": None}


def test_minor_without_guardian_cannot_consent():
    with pytest.raises(BusinessRuleError) as exc:
        resolve_signer(_patient(dob=date(2015, 5, 1)), TODAY)
    assert exc.value.code == "GUARDIAN_REQUIRED"


def test_minor_turns_18_on_birthday():
    assert resolve_signer(_patient(dob=date(2008, 10, 16)), TODAY)["signer_type"] == "self"
    with pytest.raises(BusinessRuleError):
        resolve_signer(_patient(dob=date(2008, 10, 17)), TODAY)


def test_guardian_signs_for_minor_with_reason_minor():
    signer = resolve_signer(
        _patient(dob=date(2015, 5, 1), guardian_name="Ravi Rao", guardian_relationship="father", guardian_contact="9876543210"),
        TODAY,
    )
    assert signer["signer_type"] == "guardian"
    assert signer["name"] == "Ravi Rao"
    assert signer["guardian_reason"] == "minor"
    assert signer["on_behalf_of"] == "Asha Rao"


def test_guardian_signs_for_adult_with_recorded_reason():
    signer = resolve_signer(_patient(guardian_name="Ravi Rao", guardian_reason="lacks_capacity"), TODAY)
    assert (signer["signer_type"], signer["guardian_reason"]) == ("guardian", "lacks_capacity")


def test_staff_always_sign_for_themselves():
    signer = resolve_signer(_patient(role="doctor", guardian_name="ignored", dob=None), TODAY)
    assert signer["signer_type"] == "self"


# ── rendering + hash ──────────────────────────────────────────────────────


def _values(subject, clinic_name):
    signer = resolve_signer(subject, TODAY)
    values = build_values(
        subject=subject, clinic={"clinic_name": clinic_name}, signer=signer, record={"consent_id": uuid.UUID(int=1)}, version=3, today=TODAY
    )
    return values, signer["signer_type"]


def test_render_fills_placeholders_in_text_and_checkbox_labels():
    values, signer_type = _values(_patient(), "Anava Indiranagar")
    out = render(TEMPLATE, values, signer_type, times=None)
    assert out["title"] == "Consent for Asha Rao"
    assert out["content"] == "I, Asha Rao, agree to care at Anava Indiranagar."
    assert out["clauses"][0]["label"] == "Process data of Asha Rao"
    assert "full_html" not in out  # v2 template: hash formula unchanged


def test_hash_is_stable_and_covers_checkbox_labels():
    values, st = _values(_patient(), "C")
    a = render(TEMPLATE, values, st, times=None)
    assert hash_rendered(a) == hash_rendered(render(TEMPLATE, values, st, times=None))
    changed = {**TEMPLATE, "clauses": [{**TEMPLATE["clauses"][0], "label": "Other"}, TEMPLATE["clauses"][1]]}
    assert hash_rendered(a) != hash_rendered(render(changed, values, st, times=None))
    assert re.fullmatch(r"[0-9a-f]{64}", hash_rendered(a))


# ── checkboxes ────────────────────────────────────────────────────────────


CLAUSES = [{"code": "data", "label": "A", "required": True}, {"code": "research", "label": "B", "required": False}]


def test_required_box_must_be_ticked():
    with pytest.raises(ValidationError) as exc:
        build_responses(CLAUSES, ["research"])
    assert exc.value.code == "CONSENT_REQUIRED_CLAUSE"


def test_unknown_box_is_rejected():
    with pytest.raises(ValidationError) as exc:
        build_responses(CLAUSES, ["data", "marketing"])
    assert exc.value.code == "CONSENT_UNKNOWN_CLAUSE"


def test_optional_box_may_be_left_unticked_and_label_is_kept():
    assert build_responses(CLAUSES, ["data"]) == [
        {"code": "data", "label": "A", "required": True, "accepted": True},
        {"code": "research", "label": "B", "required": False, "accepted": False},
    ]


def test_template_rejects_duplicate_or_malformed_codes():
    base = {"consent_type": "patient_onboarding", "title": "t", "content": "c"}
    with pytest.raises(PydanticValidationError):
        ConsentTemplateCreate(**base, clauses=[{"code": "a_b", "label": "x"}, {"code": "a_b", "label": "y"}])
    with pytest.raises(PydanticValidationError):
        ConsentTemplateCreate(**base, clauses=[{"code": "Bad Code", "label": "x"}])
    assert ConsentTemplateCreate(**base, clauses=[{"code": "ok_1", "label": "x"}]).clauses[0].required is True


# ── front-desk guardian rules ─────────────────────────────────────────────


def _reg(**kw):
    return {"dob": date(1990, 1, 1), "guardian_name": "Ravi", "guardian_contact": "9876543210", **kw}


def test_adult_may_choose_a_guardian_without_a_reason():
    check_guardian_login(_reg(), "+919876543210", "mobile")  # no GUARDIAN_REASON_REQUIRED any more


def test_adult_with_guardian_and_no_reason_is_signed_by_the_guardian():
    signer = resolve_signer(_patient(guardian_name="Ravi Rao", guardian_reason="patient_requested"), TODAY)
    assert (signer["signer_type"], signer["guardian_reason"]) == ("guardian", "patient_requested")


def test_login_mobile_must_be_the_guardians():
    with pytest.raises(ValidationError) as exc:
        check_guardian_login(_reg(dob=date(2015, 1, 1)), "+919999999999", "mobile")
    assert exc.value.code == "GUARDIAN_LOGIN_MISMATCH"


def test_guardian_rules_pass():
    check_guardian_login(_reg(dob=date(2015, 1, 1)), "+919876543210", "mobile")
    check_guardian_login(_reg(guardian_reason="lawful_guardian"), "+919876543210", "mobile")
    check_guardian_login({"dob": date(1990, 1, 1)}, "+910000000000", "mobile")  # no guardian


# ── storage + PDF ─────────────────────────────────────────────────────────

UUID7 = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"


def test_staff_and_platform_consent_keys():
    key = s3.build_key("consent", region_id="r", clinic_id="c", staff_id="s", template="staff_onboarding", version=2)
    assert re.fullmatch(f"regions/r/clinics/c/consents/staff/s/staff_onboarding_v2_{UUID7}\\.pdf", key)
    key = s3.build_key("consent", staff_id="s", template="staff_onboarding", version=2)
    assert key.startswith("platform/consents/staff/s/")
    with pytest.raises(ValueError):
        s3.build_key("consent", region_id="r", clinic_id="c", template="t", version=1)


def test_pdf_renders_unicode_text_and_evidence():
    record = {
        "consent_id": uuid.uuid4(),
        "consent_type": "patient_onboarding",
        "rendered_title": "Patient Onboarding Consent",
        "rendered_content": "I agree — fees in ₹ and “quoted” terms.",
        "responses": [{"code": "data", "label": "I agree", "required": True, "accepted": True}],
        "signer_type": "guardian",
        "signer_name": "Ravi Rao",
        "signer_relation": "father",
        "signer_contact": "+919876543210",
        "guardian_reason": "minor",
        "signed_at": datetime(2026, 10, 16, 9, 30, tzinfo=UTC),
        "auth_method": "cognito_password_session",
        "ip_address": IPv4Address("1.2.3.4"),
        "user_agent": "Mozilla/5.0",
        "rendered_hash": "ab" * 32,
        "content_hash_at_signing": "cd" * 32,
    }
    pdf = build_consent_pdf(
        record=record, template={"version": 2, "language": "en"}, clinic_name="Anava Indiranagar", subject_name="Kiran Rao"
    )
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000


# ── migration ─────────────────────────────────────────────────────────────


def test_migration_split_keeps_plpgsql_bodies_whole():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0068_consent_v2.py"
    spec = importlib.util.spec_from_file_location("m0068", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    statements = module.split_statements(module.SQL_FILE.read_text(encoding="utf-8"))
    functions = [s for s in statements if s.startswith("CREATE OR REPLACE FUNCTION")]
    assert len(functions) == 2 and all(s.endswith("$function$") for s in functions)
    assert not any(s in ("BEGIN", "COMMIT") for s in statements)
