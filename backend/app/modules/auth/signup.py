"""Patient signup (Cognito mode), shared by the patient self-signup API
(auth/router.py) and the receptionist-assisted one (reception/router.py).

Current flow — password is chosen BEFORE the OTP:
    start   : check the contact isn't taken, then Cognito SignUp with the
              patient's real password (user UNCONFIRMED, OTP sent).
    confirm : verify the OTP AND create our DB profile in the same request
              (confirm_and_register), so there is no moment where Cognito has
              a verified user and our DB has nothing.

The only way to abandon it is before entering the OTP; a later retry resets
that unconfirmed Cognito user (start_patient_signup). The legacy flow (OTP
first, password later — still used by the Android app) keeps working through
the old endpoints; its abandoned signups are reset the same way.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import structlog
from jose import jwt as jose_jwt
from sqlalchemy import text

from app.core.db import as_system
from app.core.exceptions import AnavaException, ConflictError

logger = structlog.get_logger()


def contact_taken_error(method: str, contact: str) -> ConflictError:
    return ConflictError(
        f"{'Email' if method == 'email' else 'Phone number'} {contact!r} already in use",
        code="EMAIL_ALREADY_EXISTS" if method == "email" else "PHONE_ALREADY_EXISTS",
    )


async def reject_if_contact_taken(db, method: str, contact: str) -> None:
    """Refuse a signup whose email/phone already belongs to a profile here —
    before any OTP goes out. Cognito knows nothing about our own uniqueness.

    as_system: this runs on an anonymous request, where rls_profiles_select
    hides EVERY profile — the check used to see 0 rows even for a real,
    existing account and never fired. Email compared case-insensitively
    (Cognito usernames are)."""
    where = "lower(email) = lower(:value)" if method == "email" else "phone = :value"
    async with as_system(db):
        taken = (await db.execute(text(f"SELECT 1 FROM profiles WHERE {where}"), {"value": contact})).first()
    if taken is not None:
        raise contact_taken_error(method, contact)


async def start_patient_signup(
    db,
    *,
    contact: str,
    method: str,
    first_name: str,
    last_name: str,
    dob: str | None,
    gender: str | None,
    password: str | None,
) -> None:
    """Cognito SignUp, resetting an abandoned earlier attempt.

    A real account always has a profile row carrying its Cognito sub. So a
    Cognito user for this contact with NO profile here is an abandoned signup
    — quit before the OTP (UNCONFIRMED), or, in the legacy flow, after the
    OTP but before the password (CONFIRMED, nobody can ever log into it).
    Delete it and start over: a fresh OTP goes to the same channel, so
    ownership is proven again. A real account is never touched."""
    from app.core.cognito import delete_user, get_user_sub, sign_up_patient

    def sign_up() -> None:
        sign_up_patient(username=contact, first_name=first_name, last_name=last_name, dob=dob, gender=gender, password=password)

    try:
        await asyncio.to_thread(sign_up)
        return
    except AnavaException as exc:
        if exc.code != "ACCOUNT_ALREADY_EXISTS":
            raise
        sub = await asyncio.to_thread(get_user_sub, contact)
        if sub is None:
            raise
        async with as_system(db):
            has_profile = (await db.execute(text("SELECT 1 FROM profiles WHERE cognito_sub = :sub"), {"sub": sub})).first()
        if has_profile is not None:
            raise
        logger.info("abandoned_patient_signup_reset", contact_type=method)
    await asyncio.to_thread(delete_user, contact)
    await asyncio.to_thread(sign_up)


async def confirm_and_register(
    db,
    *,
    contact: str,
    method: str,
    code: str,
    password: str,
    registration: dict,
    self_registered: bool,
    registered_by: UUID | None = None,
    signup_ip: str | None = None,
) -> tuple[dict, dict]:
    """Verify the OTP and create the patient in ONE request.

    `registration` holds PatientService.register's demographic fields
    (everything except email/phone, which come from contact/method here).
    Returns (patient, cognito_auth_result); the caller decides whether the
    tokens start a session (patient self-signup) or are dropped
    (receptionist-assisted — the receptionist must never hold them).

    Retry-safe: if a first attempt confirmed the OTP but failed afterwards,
    a retry skips the "already confirmed" error, proves identity with the
    password, and — if the profile already exists — returns it instead of
    creating a second one."""
    from app.core.cognito import confirm_sign_up, initiate_auth
    from app.modules.patients.service import PatientService

    await asyncio.to_thread(lambda: confirm_sign_up(username=contact, code=code, allow_already_confirmed=True))
    # Proves the caller knows the password (matters on the retry path above)
    # and yields the Cognito sub the profile is keyed on.
    auth = await asyncio.to_thread(lambda: initiate_auth(username=contact, password=password))
    cognito_sub = jose_jwt.get_unverified_claims(auth["AccessToken"])["sub"]

    async with as_system(db):
        existing = (
            (
                await db.execute(
                    text(
                        "SELECT pt.patient_id, pt.profile_id FROM patients pt JOIN profiles p ON p.id = pt.profile_id "
                        "WHERE p.cognito_sub = :sub"
                    ),
                    {"sub": cognito_sub},
                )
            )
            .mappings()
            .first()
        )
    if existing is not None:
        return dict(existing), auth

    data = {
        **registration,
        # profiles.email is NOT NULL UNIQUE — a mobile-only signup has no real
        # email yet (added + verified later via /verify-channel/*), so a
        # placeholder holds the column until then.
        "email": contact if method == "email" else f"pending-{uuid4()}@no-email.local",
        "phone": contact if method == "mobile" else None,
    }
    patient = await PatientService(db).register(
        data, self_registered=self_registered, cognito_sub=cognito_sub, registered_by=registered_by, signup_ip=signup_ip
    )
    verified_column = "email_verified" if method == "email" else "phone_verified"
    await db.execute(text(f"UPDATE profiles SET {verified_column} = TRUE WHERE id = :id"), {"id": patient["profile_id"]})
    await db.commit()
    return patient, auth


# ─── Front-desk registration (Documents/design_consent_v2.md D1-D3) ───────────


def _last10(number: str | None) -> str:
    return "".join(ch for ch in number or "" if ch.isdigit())[-10:]


def normalize_login(channel: str, contact: str) -> tuple[str, str]:
    """(contact as the Cognito username, method)."""
    from app.core.exceptions import ValidationError
    from app.modules.auth.signup_security import normalize_phone

    if channel == "email":
        contact = contact.strip().lower()
        if "@" not in contact:
            raise ValidationError("Enter a valid email address", code="EMAIL_INVALID")
        return contact, "email"
    return normalize_phone(contact), "mobile"


def check_guardian_login(registration: dict, contact: str, method: str) -> None:
    """A guardian holds the patient's login: they receive the temporary
    password and sign the consent, so the login mobile must be theirs. An
    adult may have a guardian with no stated reason — PatientService.register
    records it as patient_requested (design_consent_v2.md D3)."""
    from app.core.exceptions import ValidationError

    if not registration.get("guardian_name"):
        return
    if method == "mobile" and _last10(registration.get("guardian_contact")) != _last10(contact):
        raise ValidationError(
            "The login mobile number must be the guardian's — they receive the login details and give consent",
            code="GUARDIAN_LOGIN_MISMATCH",
        )


async def register_patient_at_clinic(db, *, channel: str, contact: str, registration: dict, registered_by: UUID) -> tuple[dict, str, str]:
    """No password, no OTP, no consent at the desk. The patient row is
    written first, then Cognito creates the login and sends a temporary
    password to the patient's own phone/email (attach_patient_login deletes
    that login again if this request rolls back). Returns (patient, contact,
    method)."""
    from app.config import get_settings
    from app.core.cognito import attach_patient_login
    from app.modules.patients.service import PatientService

    contact, method = normalize_login(channel, contact)
    check_guardian_login(registration, contact, method)
    await reject_if_contact_taken(db, method, contact)
    data = {
        **registration,
        # profiles.email is NOT NULL UNIQUE — a mobile login has no email yet.
        "email": contact if method == "email" else f"pending-{uuid4()}@no-email.local",
        "phone": contact if method == "mobile" else None,
    }
    patient = await PatientService(db).register(data, self_registered=False, registered_by=registered_by)
    if get_settings().auth_mode == "cognito":
        await attach_patient_login(
            db,
            profile_id=patient["profile_id"],
            contact=contact,
            method=method,
            first_name=registration["first_name"],
            last_name=registration["last_name"],
        )
    verified_column = "email_verified" if method == "email" else "phone_verified"
    await db.execute(text(f"UPDATE profiles SET {verified_column} = TRUE WHERE id = :id"), {"id": str(patient["profile_id"])})
    return patient, contact, method


async def resend_clinic_login(db, *, patient_id: UUID, new_contact: str | None) -> tuple[str, str]:
    """New temporary password for a patient who has not logged in yet,
    optionally to a corrected phone/email (same channel). Returns (contact,
    method)."""
    from app.core.cognito import attach_patient_login, delete_user, resend_patient_login, user_status
    from app.core.exceptions import ConflictError, NotFoundError
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(db).get(patient_id)
    if patient is None:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
    method = "mobile" if patient["phone"] else "email"
    current = patient["phone"] or patient["email"]
    status = await asyncio.to_thread(user_status, current)
    if status not in (None, "FORCE_CHANGE_PASSWORD"):
        raise ConflictError(
            "The patient has already set their password — they can use 'Forgot password' on the login page",
            code="LOGIN_ALREADY_ACTIVATED",
        )
    contact = current
    if new_contact:
        contact, new_method = normalize_login("email" if method == "email" else "phone", new_contact)
        if new_method != method:
            raise ConflictError("The corrected contact must be the same kind (phone or email)", code="LOGIN_CHANNEL_MISMATCH")
    if contact == current and status is not None:
        await asyncio.to_thread(lambda: resend_patient_login(contact=current, method=method))
        return current, method

    if contact != current:
        await reject_if_contact_taken(db, method, contact)
        column = "phone" if method == "mobile" else "email"
        async with as_system(db):
            await db.execute(text(f"UPDATE profiles SET {column} = :c WHERE id = :id"), {"c": contact, "id": str(patient["profile_id"])})
            if method == "mobile" and _last10(patient.get("guardian_contact")) == _last10(current):
                await db.execute(
                    text("UPDATE patients SET guardian_contact = :g WHERE patient_id = :pid"),
                    {"g": _last10(contact), "pid": str(patient_id)},
                )
        if status is not None:
            await asyncio.to_thread(delete_user, current)
    await attach_patient_login(
        db,
        profile_id=patient["profile_id"],
        contact=contact,
        method=method,
        first_name=patient["first_name"],
        last_name=patient["last_name"],
    )
    return contact, method
