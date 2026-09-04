from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import RequestContext, get_db
from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import require_role
from app.modules.clinical import schemas as s
from app.modules.clinical.service import ProtocolRequestService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
_CA_ROLES = ("clinical_assistant", "clinic_admin", "regional_admin", "super_admin")


# ---------------------------------------------------- protocol requests --
@router.post("/assessment-protocol-requests", response_model=s.ProtocolRequestRead, status_code=201)
async def create_protocol_request(
    body: s.ProtocolRequestCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("clinical_assistant", "super_admin")),
):
    return await ProtocolRequestService(db).create(body.model_dump(), clinical_assistant_id=UUID(ctx.user_id))


@router.get("/assessment-protocol-requests", response_model=list[s.ProtocolRequestRead])
async def list_protocol_requests(
    patient_id: UUID | None = None,
    status: str | None = None,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await ProtocolRequestService(db).list(patient_id=patient_id, status=status)


@router.get("/assessment-protocol-requests/{request_id}", response_model=s.ProtocolRequestRead)
async def get_protocol_request(request_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF))):
    return await ProtocolRequestService(db).get(request_id)


@router.patch("/assessment-protocol-requests/{request_id}/decision", response_model=s.ProtocolRequestRead)
async def decide_protocol_request(
    request_id: UUID,
    body: s.ProtocolRequestDecision,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("doctor", "super_admin")),
):
    return await ProtocolRequestService(db).decide(request_id, decision=body.decision, doctor_notes=body.doctor_notes)


# ---------------------------------------------------------- own profile --
# Same pattern as reception/router.py's /me — a plain read/write of the
# caller's own profiles row, scoped to clinical_assistant here instead of
# receptionist. No CA-specific fields exist anywhere in the schema (no
# employee_id/department/shift, checked directly, not assumed), so this is
# identical logic under a different role gate rather than a new concept.


@router.get("/clinical-assistant/me", response_model=s.MyProfileResponse)
async def get_my_profile(db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_CA_ROLES))) -> s.MyProfileResponse:
    row = (
        (
            await db.execute(
                text(
                    "SELECT id, first_name, last_name, email, phone, role, is_active, "
                    "gender, dob, address, city, state, country, pincode, language_pref "
                    "FROM profiles WHERE id = :id"
                ),
                {"id": ctx.user_id},
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
        gender=row["gender"],
        dob=row["dob"],
        address=row["address"],
        city=row["city"],
        state=row["state"],
        country=row["country"],
        pincode=row["pincode"],
        language_pref=row["language_pref"],
    )


@router.patch("/clinical-assistant/me", response_model=s.MyProfileResponse)
async def update_my_profile(
    body: s.UpdateMyProfileRequest, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_CA_ROLES))
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
        # then crashes with "Can't operate on closed transaction" (hit live:
        # PATCH /clinical-assistant/me 500s the instant a real diff is sent).
    return await get_my_profile(db=db, ctx=ctx)
