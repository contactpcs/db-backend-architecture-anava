"""Regression tests for the profile-completion percentage shared across
patient/doctor/CA/receptionist/admin — one computation, reused everywhere,
so a role's frontend "complete your profile" bar can trust the number."""

from app.core.profile_completion import (
    ADMIN_FIELDS,
    CLINICAL_ASSISTANT_FIELDS,
    DOCTOR_FIELDS,
    PATIENT_FIELDS,
    RECEPTIONIST_FIELDS,
    compute_completion_percentage,
    compute_missing_fields,
)


def test_empty_fields_list_is_100_percent():
    assert compute_completion_percentage({}, ()) == 100


def test_all_fields_missing_is_zero_percent():
    assert compute_completion_percentage({}, ("first_name", "last_name")) == 0


def test_all_fields_present_is_100_percent():
    data = {"first_name": "A", "last_name": "B"}
    assert compute_completion_percentage(data, ("first_name", "last_name")) == 100


def test_blank_string_counts_as_missing():
    data = {"first_name": "A", "last_name": "   "}
    assert compute_completion_percentage(data, ("first_name", "last_name")) == 50


def test_zero_and_false_count_as_filled_not_missing():
    # weight_kg=0 or is_active=False are real values, not "unfilled" —
    # only None/blank-string should count as missing.
    data = {"weight_kg": 0, "is_active": False}
    assert compute_completion_percentage(data, ("weight_kg", "is_active")) == 100


def test_partial_rounds_to_nearest_percent():
    data = {"a": "x", "b": None, "c": None}
    assert compute_completion_percentage(data, ("a", "b", "c")) == 33


def test_doctor_fields_include_professional_credentials():
    assert "specialization" in DOCTOR_FIELDS
    assert "license_number" in DOCTOR_FIELDS
    assert "hospital_affiliation" in DOCTOR_FIELDS


def test_ca_fields_include_qualification():
    assert "qualification" in CLINICAL_ASSISTANT_FIELDS


def test_receptionist_and_admin_have_no_role_specific_fields():
    assert set(RECEPTIONIST_FIELDS) == set(ADMIN_FIELDS)


def test_patient_fields_include_full_medical_block():
    for f in ("blood_group", "weight_kg", "height_ft", "height_in", "government_id", "id_type"):
        assert f in PATIENT_FIELDS


def test_patient_fields_include_verification_flags():
    assert "email_verified" in PATIENT_FIELDS
    assert "phone_verified" in PATIENT_FIELDS


def test_verified_flag_false_counts_as_missing_unlike_other_booleans():
    # email_verified/phone_verified: False means genuinely incomplete.
    data = {"email_verified": False, "phone_verified": True}
    assert compute_completion_percentage(data, ("email_verified", "phone_verified")) == 50
    assert compute_missing_fields(data, ("email_verified", "phone_verified")) == ["email_verified"]


def test_verified_flag_missing_key_counts_as_missing():
    assert compute_missing_fields({}, ("email_verified",)) == ["email_verified"]


def test_compute_missing_fields_lists_only_unfilled():
    data = {"a": "x", "b": None, "c": "  "}
    assert compute_missing_fields(data, ("a", "b", "c")) == ["b", "c"]
