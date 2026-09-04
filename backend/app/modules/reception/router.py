"""Adapter router for `API_ENDPOINTS (2).md` §3 (registration), §4.1 (patient
list), §7 (approvals), §11 (staff profile, partial) — see
reception/schemas.py's module docstring for what this deliberately does not
cover and why."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import RequestContext, get_db
from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError, ValidationError
from app.core.permissions import require_role
from app.modules.reception import schemas as s

router = APIRouter()

_RECEPTION_ROLES = ("receptionist", "clinic_admin", "regional_admin", "super_admin")


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthenticationError("Authorization header required", code="AUTHORIZATION_HEADER_REQUIRED")
    return auth_header.removeprefix("Bearer ").strip()


def _age(dob: date | None) -> int | None:
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _initials(first_name: str, last_name: str) -> str:
    return f"{first_name[:1]}{last_name[:1]}".upper()


@router.post("/registrations/send-code", response_model=s.SendCodeResponse, status_code=200)
async def send_verification_code(
    body: s.SendCodeRequest, _ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.SendCodeResponse:
    """Wraps the existing Cognito SignUp call (same function the receptionist-
    signup wizard already uses). verification_id is the contact itself — our
    system has no separate server-side verification-state table to hand back
    an opaque ID for; Cognito tracks confirmation state by username."""
    from app.core.cognito import sign_up_patient

    sign_up_patient(
        username=body.contact,
        first_name=body.first_name,
        last_name=body.last_name,
        dob=body.dob.isoformat() if body.dob else None,
        gender=body.gender,
    )
    return s.SendCodeResponse(
        verification_id=body.contact,
        channel=body.channel,
        contact=body.contact,
        sent_at=datetime.now(UTC),
    )


@router.post("/registrations/verify-code", status_code=200, response_model=s.VerifyCodeResponse)
async def verify_code(body: s.VerifyCodeRequest, _ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))) -> s.VerifyCodeResponse:
    """registration_token is also just the contact — our real "proof of
    verification" is Cognito's CONFIRMED status itself, re-checked implicitly
    when /patients below calls set_patient_password."""
    from app.core.cognito import confirm_sign_up

    contact = body.verification_id
    confirm_sign_up(username=contact, code=body.code)
    return s.VerifyCodeResponse(
        verification_id=contact,
        channel="email" if "@" in contact else "phone",
        contact=contact,
        verified=True,
        verified_at=datetime.now(UTC),
        registration_token=contact,
    )


@router.get("/registrations/password-policy", response_model=s.PasswordPolicyResponse)
async def password_policy(_ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))) -> s.PasswordPolicyResponse:
    """Matches the real constraint enforced at /patients below (min 8 chars,
    nothing else) — not the doc's own aspirational rule set."""
    return s.PasswordPolicyResponse()


@router.post("/patients", response_model=s.RegisterPatientResponse, status_code=201)
async def register_patient(
    body: s.RegisterPatientRequest, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.RegisterPatientResponse:
    """Completes the registration — same underlying mechanism as
    /auth/patients/receptionist-signup/complete (set_patient_password +
    PatientService.register), just accepting this spec's nested request
    shape and returning its response shape."""
    if not body.consent.accepted:
        raise ValidationError("Consent must be accepted", code="CONSENT_REQUIRED")

    from app.core.cognito import set_patient_password
    from app.modules.patients.service import PatientService

    contact = body.registration_token
    method = "email" if "@" in contact else "mobile"
    cognito_sub = set_patient_password(username=contact, password=body.password)

    data = {
        "first_name": body.personal.first_name,
        "last_name": body.personal.last_name,
        "dob": body.personal.date_of_birth,
        "gender": body.personal.gender,
        "address": body.address.street,
        "city": body.address.city,
        "state": body.address.state,
        "country": body.address.country,
        "pincode": body.address.pincode,
        "primary_clinic_id": str(body.clinic_id),
        "email": contact if method == "email" else f"pending-{cognito_sub}@no-email.local",
        "phone": contact if method == "mobile" else None,
        "guardian_name": body.guardian.name if body.guardian else None,
        "guardian_relationship": body.guardian.relation if body.guardian else None,
        "guardian_contact": body.guardian.contact_number if body.guardian else None,
    }
    patient = await PatientService(db).register(data, self_registered=False, cognito_sub=cognito_sub, registered_by=UUID(ctx.user_id))
    if method == "email":
        from sqlalchemy import text

        await db.execute(text("UPDATE profiles SET email_verified = TRUE WHERE id = :id"), {"id": patient["profile_id"]})
    await db.commit()

    return s.RegisterPatientResponse(
        patient_id=patient["patient_id"],
        full_name=f"{body.personal.first_name} {body.personal.last_name}",
        login_id=contact,
        login_channel=method,
        registration_status="verified",
        consent_status="signed" if body.consent.signature_captured else "accepted",
        created_at=patient.get("created_at") or datetime.now(UTC),
    )


@router.get("/patients", response_model=s.PatientListResponse)
async def list_patients(
    page: int = 1, page_size: int = 20, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.PatientListResponse:
    """last_visit/next_appointment are always null — depend on the
    appointments module, out of scope for this adapter (see schemas.py)."""
    from app.modules.patients.service import PatientService

    clinic_id = UUID(ctx.clinic_id) if ctx.role in ("receptionist", "clinic_admin") and ctx.clinic_id else None
    all_patients = await PatientService(db).list(clinic_id=clinic_id)
    total = len(all_patients)
    start = (page - 1) * page_size
    page_items = all_patients[start : start + page_size]

    items = [
        s.PatientListItem(
            patient_id=p["patient_id"],
            full_name=f"{p['first_name']} {p['last_name']}",
            initials=_initials(p["first_name"], p["last_name"]),
            age=_age(p["dob"]),
            gender=p["gender"],
            phone=p["phone"],
            assigned_doctor=p.get("doctor_name"),
            registration_status=p["registration_status"],
        )
        for p in page_items
    ]
    total_pages = max(1, math.ceil(total / page_size))
    return s.PatientListResponse(
        items=items,
        pagination=s.Pagination(
            page=page, page_size=page_size, total_items=total, total_pages=total_pages, has_next=page < total_pages, has_previous=page > 1
        ),
    )


@router.get("/registrations", response_model=s.RegistrationListResponse)
async def list_registrations(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES)),
) -> s.RegistrationListResponse:
    """No separate registration_requests table — a "registration" here is a
    self-registered patients row. Receptionist-assisted registrations
    (self_registered=False, this same module's /patients above) never
    appear here, matching the original spec's own note that they bypass
    this queue."""
    from app.modules.patients.service import PatientService

    clinic_id = UUID(ctx.clinic_id) if ctx.role in ("receptionist", "clinic_admin") and ctx.clinic_id else None
    rows = await PatientService(db).list(clinic_id=clinic_id, approval_status=status)
    rows = [r for r in rows if r.get("self_registered")]
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    items = [
        s.RegistrationListItem(
            registration_id=r["patient_id"],
            full_name=f"{r['first_name']} {r['last_name']}",
            contact=r["phone"] or r["email"],
            contact_type="phone" if r["phone"] else "email",
            submitted_on=r["created_at"].date().isoformat() if r.get("created_at") else None,
            status=r["approval_status"],
            linked_patient_id=r["patient_id"] if r["approval_status"] == "approved" else None,
            allowed_actions=["approve", "reject"] if r["approval_status"] == "pending" else ["view"],
        )
        for r in page_rows
    ]
    total_pages = max(1, math.ceil(total / page_size))
    return s.RegistrationListResponse(
        items=items,
        pagination=s.Pagination(
            page=page, page_size=page_size, total_items=total, total_pages=total_pages, has_next=page < total_pages, has_previous=page > 1
        ),
    )


@router.post("/registrations/{registration_id}/approve", response_model=s.ApproveRejectResponse)
async def approve_registration(
    registration_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.ApproveRejectResponse:
    from app.modules.patients.service import PatientService

    service = PatientService(db)
    patient = await service.decide_approval(registration_id, decision="approved", decided_by=UUID(ctx.user_id), rejection_reason=None)
    await db.commit()
    return s.ApproveRejectResponse(
        registration_id=registration_id,
        status="approved",
        patient_id=patient["patient_id"],
        full_name=f"{patient['first_name']} {patient['last_name']}",
    )


@router.post("/registrations/{registration_id}/reject", response_model=s.ApproveRejectResponse)
async def reject_registration(
    registration_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.ApproveRejectResponse:
    from app.modules.patients.service import PatientService

    await PatientService(db).decide_approval(registration_id, decision="rejected", decided_by=UUID(ctx.user_id), rejection_reason=None)
    await db.commit()
    return s.ApproveRejectResponse(registration_id=registration_id, status="rejected")


@router.get("/me", response_model=s.MyProfileResponse)
async def get_my_profile(db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))) -> s.MyProfileResponse:
    row = (
        (
            await db.execute(
                text("SELECT id, first_name, last_name, email, phone, role, is_active FROM profiles WHERE id = :id"), {"id": ctx.user_id}
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise NotFoundError("Profile not found", code="PROFILE_NOT_FOUND")
    clinic_name = None
    if ctx.clinic_id:
        clinic_row = (
            (await db.execute(text("SELECT clinic_name FROM clinics WHERE clinic_id = :id"), {"id": ctx.clinic_id})).mappings().first()
        )
        clinic_name = clinic_row["clinic_name"] if clinic_row else None
    return s.MyProfileResponse(
        profile_id=row["id"],
        full_name=f"{row['first_name']} {row['last_name']}",
        email=row["email"],
        phone=row["phone"],
        role=row["role"],
        clinic=clinic_name,
        is_active=row["is_active"],
    )


@router.patch("/me", response_model=s.MyProfileResponse)
async def update_my_profile(
    body: s.UpdateMyProfileRequest, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.MyProfileResponse:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        try:
            await db.execute(text(f"UPDATE profiles SET {set_clause} WHERE id = :id"), {**updates, "id": ctx.user_id})
        except IntegrityError as exc:
            raise ConflictError("Email already in use", code="EMAIL_IN_USE") from exc
        # No explicit commit here — get_db() already wraps the whole request
        # in one session.begin() transaction that commits when the request
        # finishes successfully. An explicit commit() closes that
        # transaction early, and the get_my_profile() re-fetch right below
        # then crashes with "Can't operate on closed transaction" (same bug
        # found live on clinical/router.py's copy of this exact pattern).
    return await get_my_profile(db=db, ctx=ctx)


@router.post("/me/change-password", response_model=s.ChangePasswordResponse)
async def change_my_password(
    body: s.ChangePasswordRequest, request: Request, _ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.ChangePasswordResponse:
    if body.new_password != body.confirm_password:
        raise ValidationError("Passwords do not match", code="PASSWORD_MISMATCH")
    from app.core.cognito import change_password

    change_password(access_token=_bearer_token(request), previous_password=body.current_password, new_password=body.new_password)
    return s.ChangePasswordResponse(changed_at=datetime.now(UTC))


@router.get("/patients/{patient_id}", response_model=s.PatientProfileResponse)
async def get_patient_profile(
    patient_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.PatientProfileResponse:
    """Real fields only — see PatientProfileResponse's own docstring for
    what the original spec wanted here that has no backing."""
    from app.modules.patients.service import PatientService

    p = await PatientService(db).get(patient_id)
    return s.PatientProfileResponse(
        patient_id=p["patient_id"],
        full_name=f"{p['first_name']} {p['last_name']}",
        is_active=p["profile_is_active"],
        registration_status=p["registration_status"],
        approval_status=p["approval_status"],
        personal=s.PatientPersonal(date_of_birth=p["dob"], age=_age(p["dob"]), gender=p["gender"]),
        contact=s.PatientContact(phone=p["phone"], email=p["email"], address=p["address"]),
        assigned_doctor=p.get("doctor_name"),
        guardian_name=p.get("guardian_name"),
        guardian_relationship=p.get("guardian_relationship"),
        guardian_contact=p.get("guardian_contact"),
    )


@router.get("/notifications/unread-count", response_model=s.UnreadCountResponse)
async def get_unread_count(db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))) -> s.UnreadCountResponse:
    from app.modules.notifications.service import NotificationService

    count = await NotificationService(db).unread_count(UUID(ctx.user_id))
    return s.UnreadCountResponse(total_unread=count)


@router.get("/notifications", response_model=s.NotificationListResponse)
async def list_notifications(
    unread_only: bool = False, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.NotificationListResponse:
    """category/message are adapted from this backend's real type/title/body
    fields — tone and icon (pure display hints in the original spec) are not
    included, no such concept exists here."""
    from app.modules.notifications.service import NotificationService

    service = NotificationService(db)
    rows = await service.list_for_user(UUID(ctx.user_id), unread_only=unread_only)
    unread = sum(1 for r in rows if not r["is_read"])
    items = [
        s.NotificationItem(
            notification_id=r["notification_id"],
            category=r["type"],
            message=r["title"] if not r.get("body") else f"{r['title']} — {r['body']}",
            is_read=r["is_read"],
            when=r["created_at"],
        )
        for r in rows
    ]
    return s.NotificationListResponse(items=items, counts=s.NotificationCounts(unread=unread, total=len(rows)))


@router.patch("/notifications/{notification_id}", response_model=s.ToggleReadResponse)
async def toggle_notification_read(
    notification_id: UUID, body: s.ToggleReadRequest, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.ToggleReadResponse:
    """Mark-as-read only — see ToggleReadRequest's docstring. is_read=false
    is rejected rather than silently ignored."""
    if not body.is_read:
        raise ValidationError("Marking a notification unread is not supported", code="UNSUPPORTED_OPERATION")
    from app.modules.notifications.service import NotificationService

    service = NotificationService(db)
    await service.mark_read(UUID(ctx.user_id), [notification_id])
    # No explicit commit — get_db() already wraps the whole request in one
    # transaction that commits when the request finishes; committing early
    # here closes it before the unread_count() re-query right below, which
    # then crashes with "Can't operate on closed transaction" (same class of
    # bug found live on /me and /notifications/mark-all-read).
    remaining = await service.unread_count(UUID(ctx.user_id))
    return s.ToggleReadResponse(notification_id=notification_id, is_read=True, total_unread=remaining)


@router.post("/notifications/mark-all-read", response_model=s.MarkAllReadResponse)
async def mark_all_notifications_read(
    db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))
) -> s.MarkAllReadResponse:
    from app.modules.notifications.service import NotificationService

    service = NotificationService(db)
    marked = await service.mark_read(UUID(ctx.user_id), None)
    # No explicit commit — see toggle_notification_read above for why.
    remaining = await service.unread_count(UUID(ctx.user_id))
    return s.MarkAllReadResponse(marked_count=marked, total_unread=remaining)


@router.get("/doctors", response_model=s.DoctorListResponse)
async def list_doctors(db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))) -> s.DoctorListResponse:
    """department in the response is this backend's real `specialization`
    column — same slot the original spec's UI evidence uses it for
    ("Dr. James Cooper — Psychiatry")."""
    from app.modules.staff.service import DoctorService

    clinic_id = UUID(ctx.clinic_id) if ctx.role in ("receptionist", "clinic_admin") and ctx.clinic_id else None
    doctors = await DoctorService(db).list(clinic_id=clinic_id)
    items = [
        s.DoctorListItem(
            doctor_id=d["doctor_id"],
            name=f"{d['first_name']} {d['last_name']}",
            department=d.get("specialization"),
            label=f"{d['first_name']} {d['last_name']} — {d.get('specialization') or 'General'}",
        )
        for d in doctors
    ]
    return s.DoctorListResponse(items=items)


@router.get("/enums", response_model=s.EnumsResponse)
async def list_enums(_ctx: RequestContext = Depends(require_role(*_RECEPTION_ROLES))) -> s.EnumsResponse:
    """Values match exactly what this backend's own Pydantic validators
    accept (Field(pattern=...) on gender/guardian_relationship) — not the
    original spec's own list, which includes fields (identity_type) this
    system doesn't have."""
    return s.EnumsResponse(
        gender=[
            s.EnumOption(value="female", label="Female"),
            s.EnumOption(value="male", label="Male"),
            s.EnumOption(value="other", label="Other"),
        ],
        relationship=[
            s.EnumOption(value="parent", label="Parent"),
            s.EnumOption(value="spouse", label="Spouse"),
            s.EnumOption(value="sibling", label="Sibling"),
            s.EnumOption(value="child", label="Child"),
            s.EnumOption(value="legal_guardian", label="Legal Guardian"),
            s.EnumOption(value="other", label="Other"),
        ],
    )
