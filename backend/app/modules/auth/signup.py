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
        raise ConflictError(
            f"{'Email' if method == 'email' else 'Phone number'} {contact!r} already in use",
            code="EMAIL_ALREADY_EXISTS" if method == "email" else "PHONE_ALREADY_EXISTS",
        )


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
    patient = await PatientService(db).register(data, self_registered=self_registered, cognito_sub=cognito_sub, registered_by=registered_by)
    verified_column = "email_verified" if method == "email" else "phone_verified"
    await db.execute(text(f"UPDATE profiles SET {verified_column} = TRUE WHERE id = :id"), {"id": patient["profile_id"]})
    await db.commit()
    return patient, auth
