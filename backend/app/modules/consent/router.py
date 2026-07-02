from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.exceptions import BusinessRuleError
from app.core.permissions import require_role
from app.modules.consent import schemas as s
from app.modules.consent.service import ConsentRecordService, ConsentTemplateService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


@router.get("/consent-templates", response_model=list[s.ConsentTemplateRead])
async def list_templates(db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await ConsentTemplateService(db).list()


@router.post("/consent-records", response_model=s.ConsentRecordRead, status_code=201)
async def create_consent_record(
    body: s.ConsentRecordCreate, db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    if not body.patient_id and not body.staff_id:
        raise BusinessRuleError("Either patient_id or staff_id must be set", code="CONSENT_SUBJECT_REQUIRED")
    return await ConsentRecordService(db).create(
        consent_type=body.consent_type, patient_id=body.patient_id, staff_id=body.staff_id, clinic_id=body.clinic_id
    )


@router.get("/consent-records", response_model=list[s.ConsentRecordRead])
async def list_consent_records(
    patient_id: UUID | None = None, staff_id: UUID | None = None, clinic_id: UUID | None = None,
    db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    return await ConsentRecordService(db).list(patient_id=patient_id, staff_id=staff_id, clinic_id=clinic_id)


@router.get("/consent-records/{consent_id}", response_model=s.ConsentRecordRead)
async def get_consent_record(consent_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return await ConsentRecordService(db).get(consent_id)


@router.patch("/consent-records/{consent_id}/status", response_model=s.ConsentRecordRead)
async def update_consent_status(
    consent_id: UUID, body: s.ConsentStatusUpdate, db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    service = ConsentRecordService(db)
    if body.status == "signed":
        sign = body.sign or s.ConsentSignRequest(signature_data="")
        return await service.sign(
            consent_id, signed_by=UUID(ctx.user_id), witness_id=sign.witness_id,
            signature_data=sign.signature_data, ip_address=sign.ip_address,
        )
    return await service.revoke(consent_id, revoked_by=UUID(ctx.user_id))
