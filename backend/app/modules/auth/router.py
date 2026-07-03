from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.config import get_settings
from app.core.db import RequestContext, engine, get_db
from app.core.exceptions import NotFoundError
from app.core.permissions import get_current_context
from app.core.security import create_local_token
from app.modules.auth.schemas import (
    CurrentUserRead,
    LocalLoginRequest,
    PublicPatientRegister,
    PublicPatientRegisterResponse,
    TokenResponse,
)

router = APIRouter()
settings = get_settings()


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
    """Public self-registration — no auth required (see PUBLIC_PATHS in
    core/middleware.py). Creates an inactive patient profile and logs them
    in immediately so they can continue the rest of the wizard (disease
    selection, consent, anamnesis, PRS) while still inactive; only a
    receptionist's later approval (PATCH /patients/{id}/approval) flips the
    account live."""
    from app.modules.patients.service import PatientService

    data = body.model_dump()
    data["primary_clinic_id"] = str(data["primary_clinic_id"])
    patient = await PatientService(db).register(data, self_registered=True)
    token = create_local_token(sub=patient["cognito_sub"])
    return PublicPatientRegisterResponse(access_token=token, patient_id=patient["patient_id"])


@router.post("/local-login", response_model=TokenResponse)
async def local_login(body: LocalLoginRequest) -> TokenResponse:
    """Dev/test-only endpoint — issues a token for a seeded profile without
    needing a Cognito account. Disabled once auth_mode='cognito' (Stage 13);
    real clients authenticate directly against Cognito instead."""
    if settings.auth_mode != "local":
        raise HTTPException(status_code=404, detail="Not found")
    if not body.cognito_sub and not body.email:
        raise HTTPException(status_code=422, detail="cognito_sub or email required")

    cognito_sub = body.cognito_sub
    if not cognito_sub:
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT cognito_sub FROM profiles WHERE email = :email"), {"email": body.email})
            ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="No profile found for this email")
        cognito_sub = row["cognito_sub"]

    token = create_local_token(sub=cognito_sub)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUserRead)
async def get_current_user(ctx: RequestContext = Depends(get_current_context)) -> CurrentUserRead:
    """The 'who am I' every authenticated role can call — unlike /_internal/whoami
    (debug-only, super_admin-gated), this is the real profile-fetch endpoint
    a frontend calls right after login to know who's signed in."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, email, first_name, last_name, role FROM profiles WHERE id = :id"),
                {"id": ctx.user_id},
            )
        ).mappings().first()
    if not row:
        raise NotFoundError("Profile not found", code="PROFILE_NOT_FOUND")
    return CurrentUserRead(
        id=row["id"], email=row["email"], first_name=row["first_name"], last_name=row["last_name"],
        role=row["role"], clinic_id=UUID(ctx.clinic_id) if ctx.clinic_id else None,
        region_id=UUID(ctx.region_id) if ctx.region_id else None,
        is_active=ctx.is_active,
        consent_type_required=None if ctx.is_active else ("patient_onboarding" if row["role"] == "patient" else "staff_onboarding"),
    )
