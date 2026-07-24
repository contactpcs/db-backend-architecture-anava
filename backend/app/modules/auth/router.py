from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.config import get_settings
from app.core.db import RequestContext, engine, get_db
from app.core.exceptions import AuthenticationError, NotFoundError, ValidationError
from app.core.permissions import get_current_context
from app.core.security import create_local_token
from app.modules.auth.schemas import (
    CurrentUserRead,
    LocalLoginRequest,
    LoginRequest,
    NewPasswordRequest,
    PatientSignupComplete,
    PatientSignupResend,
    PatientSignupStart,
    PatientSignupVerify,
    PublicPatientRegister,
    PublicPatientRegisterResponse,
    TokenResponse,
    VerifyChannelConfirm,
    VerifyChannelStart,
)

router = APIRouter()
settings = get_settings()


@router.get("/config")
async def get_auth_config() -> dict:
    """Public — tells the frontend which endpoints to call (the OTP signup
    wizard + /auth/login in cognito mode, vs the single-step /auth/register +
    /auth/local-login in local dev). No secrets here, just the mode string."""
    return {"auth_mode": settings.auth_mode}


@router.get("/clinics")
async def list_public_clinics() -> list[dict]:
    """Public clinic picker for the self-registration form — no auth
    required (see PUBLIC_PATHS in core/middleware.py). Deliberately minimal
    fields only (no phone/email/admin info). Excludes only
    pending_closure/closed clinics — matches the same "is this clinic open
    for new people" check patients/service.py already uses
    (_ensure_clinic_ready_for_staff), not a stricter active-only rule."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT clinic_id, clinic_name, city, state, address FROM clinics "
                    "WHERE status NOT IN ('pending_closure', 'closed') ORDER BY clinic_name"
                )
            )
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/register", response_model=PublicPatientRegisterResponse, status_code=201)
async def register_patient_public(body: PublicPatientRegister, db=Depends(get_db)) -> PublicPatientRegisterResponse:
    """Public self-registration — LOCAL DEV ONLY (404s once auth_mode ==
    'cognito'; real patients go through the OTP wizard below instead, which
    this single-step, no-OTP shape can't represent). No auth required (see
    PUBLIC_PATHS in core/middleware.py). Creates an inactive patient profile
    and logs them in immediately so they can continue the rest of the wizard
    (disease selection, consent, anamnesis, PRS) while still inactive; only
    a receptionist's later approval (PATCH /patients/{id}/approval) flips
    the account live."""
    if settings.auth_mode != "local":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.modules.patients.service import PatientService

    data = body.model_dump()
    data["primary_clinic_id"] = str(data["primary_clinic_id"])
    patient = await PatientService(db).register(data, self_registered=True)
    token = create_local_token(sub=patient["cognito_sub"])
    return PublicPatientRegisterResponse(access_token=token, patient_id=patient["patient_id"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Real password login (Stage 13) — calls Cognito's InitiateAuth
    directly with the email/password from our own login form (no Hosted-UI
    redirect). Works for staff and patients alike; username may be an email
    or a phone number. 404s in local mode; use /auth/local-login there instead."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import initiate_auth

    result = initiate_auth(username=body.username, password=body.password)
    return TokenResponse(access_token=result["AccessToken"], refresh_token=result.get("RefreshToken"))


@router.post("/login/new-password", response_model=TokenResponse)
async def login_new_password(body: NewPasswordRequest) -> TokenResponse:
    """Completes the NEW_PASSWORD_REQUIRED challenge — a staff account's
    first login after AdminCreateUser's auto-emailed temp password. session
    is the value the /auth/login 400 (code NEW_PASSWORD_REQUIRED) returned."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import respond_new_password

    result = respond_new_password(username=body.username, new_password=body.new_password, session=body.session)
    return TokenResponse(access_token=result["AccessToken"], refresh_token=result.get("RefreshToken"))


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthenticationError("Authorization header required", code="AUTHORIZATION_HEADER_REQUIRED")
    return auth_header.removeprefix("Bearer ").strip()


@router.post("/patients/signup/start", status_code=204)
async def patient_signup_start(body: PatientSignupStart) -> None:
    """Step 1 of the real patient signup wizard — starts Cognito's SignUp,
    which auto-sends the OTP to whichever channel (email or phone) the
    patient chose. 404s in local mode (use /auth/register there instead —
    no OTP step needed for local testing)."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import sign_up_patient

    sign_up_patient(
        username=body.contact, first_name=body.first_name, last_name=body.last_name,
        dob=body.dob.isoformat() if body.dob else None, gender=body.gender,
    )


@router.post("/patients/signup/resend", status_code=204)
async def patient_signup_resend(body: PatientSignupResend) -> None:
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import resend_confirmation_code

    resend_confirmation_code(body.contact)


@router.post("/patients/signup/verify", status_code=204)
async def patient_signup_verify(body: PatientSignupVerify) -> None:
    """Step 2 — verifies the OTP the patient just entered. Doesn't touch our
    DB at all; the wizard only writes a profiles/patients row once the
    password is set too (see /signup/complete)."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import confirm_sign_up

    confirm_sign_up(username=body.contact, code=body.code)


@router.post("/patients/signup/complete", response_model=PublicPatientRegisterResponse, status_code=201)
async def patient_signup_complete(body: PatientSignupComplete, db=Depends(get_db)) -> PublicPatientRegisterResponse:
    """Step 3 — sets the real password (overwriting SignUp's throwaway one),
    creates our own profiles/patients row with the now-real Cognito sub, and
    auto-logs the patient in. The channel they signed up with is already
    verified (ConfirmSignUp in step 2); the other one still needs the
    separate /patients/verify-channel/* round-trip post-login."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    if body.password != body.confirm_password:
        raise ValidationError("Passwords do not match", code="PASSWORD_MISMATCH")
    from app.core.cognito import initiate_auth, set_patient_password
    from app.modules.patients.service import PatientService

    cognito_sub = set_patient_password(username=body.contact, password=body.password)

    # profiles.email is NOT NULL UNIQUE — a mobile-only signup has no real
    # email yet (it's added+verified later via /verify-channel/*), so this
    # placeholder holds the column until then, same pattern as cognito_sub's
    # own 'pending-<uuid>' placeholder elsewhere in this codebase.
    data = {
        "first_name": body.first_name, "last_name": body.last_name, "dob": body.dob,
        "gender": body.gender, "address": body.address, "city": body.city, "state": body.state,
        "country": body.country, "pincode": body.pincode,
        "primary_clinic_id": str(body.primary_clinic_id),
        "email": body.contact if body.method == "email" else f"pending-{uuid4()}@no-email.local",
        "phone": body.contact if body.method == "mobile" else None,
    }
    patient = await PatientService(db).register(data, self_registered=True, cognito_sub=cognito_sub)
    if body.method == "email":
        await db.execute(text("UPDATE profiles SET email_verified = TRUE WHERE id = :id"), {"id": patient["profile_id"]})
    else:
        await db.execute(text("UPDATE profiles SET phone_verified = TRUE WHERE id = :id"), {"id": patient["profile_id"]})
    await db.commit()

    result = initiate_auth(username=body.contact, password=body.password)
    return PublicPatientRegisterResponse(access_token=result["AccessToken"], patient_id=patient["patient_id"])


@router.post("/patients/verify-channel/start", status_code=204)
async def verify_channel_start(body: VerifyChannelStart, request: Request,
                                _ctx: RequestContext = Depends(get_current_context)) -> None:
    """Post-signup: adds the channel NOT used at signup (e.g. a
    mobile-signup patient's Cognito user has no email attribute at all yet)
    and triggers its verification code in one call. Authenticated — needs
    the caller's own Cognito access token, which this reads straight off the
    Authorization header (get_current_context already validated it; this
    just needs the raw string Cognito itself wants)."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import add_and_verify_channel_start

    add_and_verify_channel_start(access_token=_bearer_token(request), attribute=body.attribute, value=body.value)


@router.post("/patients/verify-channel/confirm", status_code=204)
async def verify_channel_confirm(body: VerifyChannelConfirm, request: Request, db=Depends(get_db),
                                  ctx: RequestContext = Depends(get_current_context)) -> None:
    """On success, overwrites our own profiles.email/phone with the real
    verified value too — email in particular may still be holding the
    'pending-<uuid>@no-email.local' placeholder from a mobile-only signup."""
    if settings.auth_mode != "cognito":
        raise NotFoundError("Not found", code="NOT_FOUND")
    from app.core.cognito import verify_attribute

    verify_attribute(access_token=_bearer_token(request), attribute=body.attribute, code=body.code)
    if body.attribute == "email":
        await db.execute(
            text("UPDATE profiles SET email_verified = TRUE, email = :value WHERE id = :id"),
            {"value": body.value, "id": ctx.user_id},
        )
    else:
        await db.execute(
            text("UPDATE profiles SET phone_verified = TRUE, phone = :value WHERE id = :id"),
            {"value": body.value, "id": ctx.user_id},
        )
    await db.commit()


@router.post("/local-login", response_model=TokenResponse)
async def local_login(body: LocalLoginRequest) -> TokenResponse:
    """Dev/test-only endpoint — issues a token for a seeded profile without
    needing a Cognito account. Disabled once auth_mode='cognito' (Stage 13);
    real clients use /auth/login above instead."""
    if settings.auth_mode != "local":
        raise NotFoundError("Not found", code="NOT_FOUND")
    if not body.cognito_sub and not body.email:
        raise ValidationError("cognito_sub or email required", code="COGNITO_SUB_OR_EMAIL_REQUIRED")

    cognito_sub = body.cognito_sub
    if not cognito_sub:
        async with engine.connect() as conn:
            # Must run before the SELECT, same transaction — this is what
            # rls_profiles_select's self-lookup-by-email clause matches
            # against, since no RLS context can exist yet (this query is
            # what determines it). Same chicken-and-egg shape as the
            # cognito_sub lookup in core/middleware.py — see
            # SQL/33_fix_local_login_email_lookup_rls.sql.
            await conn.execute(text("SELECT set_config('app.current_email', :email, true)"), {"email": body.email})
            row = (
                await conn.execute(text("SELECT cognito_sub FROM profiles WHERE email = :email"), {"email": body.email})
            ).mappings().first()
        if not row:
            raise NotFoundError("No profile found for this email", code="PROFILE_NOT_FOUND")
        cognito_sub = row["cognito_sub"]

    token = create_local_token(sub=cognito_sub)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUserRead)
async def get_current_user(ctx: RequestContext = Depends(get_current_context), db=Depends(get_db)) -> CurrentUserRead:
    """The 'who am I' every authenticated role can call — unlike /_internal/whoami
    (debug-only, super_admin-gated), this is the real profile-fetch endpoint
    a frontend calls right after login to know who's signed in.

    Uses Depends(get_db) (the request-scoped, RLS-context-applied session),
    not a raw engine.connect() — the middleware already resolved ctx.user_id
    from a real row, but a *second*, separately-unscoped connection here
    would fail rls_profiles_select all over again once RLS is actually
    enforced (RDS cutover surfaced this — see
    SQL/31_fix_profile_bootstrap_lookup_rls.sql for the related middleware-
    side fix; this one didn't need a new policy, just the right session)."""
    row = (
        await db.execute(
            text("SELECT id, email, first_name, last_name, role, email_verified, phone_verified FROM profiles WHERE id = :id"),
            {"id": ctx.user_id},
        )
    ).mappings().first()
    if not row:
        raise NotFoundError("Profile not found", code="PROFILE_NOT_FOUND")

    patient_row = None
    if row["role"] == "patient":
        patient_row = (
            await db.execute(
                text(
                    "SELECT patient_id, self_registered, registration_status FROM patients WHERE profile_id = :pid"
                ),
                {"pid": row["id"]},
            )
        ).mappings().first()

    doctor_row = None
    if row["role"] == "doctor":
        doctor_row = (
            await db.execute(text("SELECT doctor_id FROM doctors WHERE profile_id = :pid"), {"pid": row["id"]})
        ).mappings().first()

    return CurrentUserRead(
        id=row["id"], email=row["email"], first_name=row["first_name"], last_name=row["last_name"],
        role=row["role"], clinic_id=UUID(ctx.clinic_id) if ctx.clinic_id else None,
        region_id=UUID(ctx.region_id) if ctx.region_id else None,
        is_active=ctx.is_active,
        consent_signed=ctx.consent_signed,
        consent_type_required=None if ctx.is_active else ("patient_onboarding" if row["role"] == "patient" else "staff_onboarding"),
        self_registered=bool(patient_row["self_registered"]) if patient_row else False,
        patient_id=patient_row["patient_id"] if patient_row else None,
        registration_status=patient_row["registration_status"] if patient_row else None,
        doctor_id=doctor_row["doctor_id"] if doctor_row else None,
        # Only meaningful for a real cognito-mode patient (the only flow that
        # ever leaves one channel unverified) — every other case (local dev,
        # staff, patients pre-dating this feature) reports True regardless of
        # the column's actual value so no "verify your email" banner ever
        # nags someone this feature was never asked of.
        email_verified=True if not (settings.auth_mode == "cognito" and row["role"] == "patient") else row["email_verified"],
        phone_verified=True if not (settings.auth_mode == "cognito" and row["role"] == "patient") else row["phone_verified"],
    )
