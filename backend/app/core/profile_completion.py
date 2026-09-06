"""Profile-completion percentage shown on every role's "my profile" screen.
One shared definition of "which fields count" per role, so every module's
get()/list() computes the same number the same way instead of each
reinventing its own field list — and so the frontend's own checklist
(profileCompletion.ts) can drop its separate client-side computation and
just consume profile_completion_missing_fields instead of drifting from
whatever this file considers complete."""

_STAFF_COMMON_FIELDS = (
    "first_name", "last_name", "email", "phone", "gender", "dob",
    "address", "city", "state", "country", "pincode", "language_pref",
)

# email_verified/phone_verified only apply to patients here — the OTP
# self-signup wizard is the one flow that leaves them meaningfully False;
# staff accounts are provisioned directly (Cognito admin-create, no OTP
# step), so those flags are always True there and add nothing.
PATIENT_FIELDS = (
    "first_name", "last_name", "email", "phone", "gender", "dob",
    "address", "city", "state", "country", "pincode",
    "emergency_contact_name", "emergency_contact_phone", "language_pref",
    "blood_group", "allergies", "occupation", "marital_status",
    "insurance_provider", "insurance_policy", "weight_kg", "height_ft",
    "height_in", "government_id", "id_type",
    "email_verified", "phone_verified",
)
DOCTOR_FIELDS = _STAFF_COMMON_FIELDS + ("specialization", "license_number", "hospital_affiliation")
CLINICAL_ASSISTANT_FIELDS = _STAFF_COMMON_FIELDS + ("qualification",)
RECEPTIONIST_FIELDS = _STAFF_COMMON_FIELDS
ADMIN_FIELDS = _STAFF_COMMON_FIELDS

# Boolean flags where the value itself IS the completion signal (True =
# done), unlike every other field where "a value was entered" is what
# counts — False/0 there (weight_kg=0, is_active=False) is still real data,
# not "missing".
_BOOLEAN_COMPLETION_FIELDS = frozenset({"email_verified", "phone_verified"})


def _is_filled(field: str, value) -> bool:
    if field in _BOOLEAN_COMPLETION_FIELDS:
        return value is True
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def compute_completion_percentage(data: dict, fields: tuple[str, ...]) -> int:
    if not fields:
        return 100
    filled = sum(1 for f in fields if _is_filled(f, data.get(f)))
    return round(filled * 100 / len(fields))


def compute_missing_fields(data: dict, fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if not _is_filled(f, data.get(f))]
