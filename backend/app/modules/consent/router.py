from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.db import RequestContext, get_db
from app.core.exceptions import BusinessRuleError
from app.core.permissions import require_role
from app.core.scoping import assert_owns_profile
from app.modules.consent import schemas as s
from app.modules.consent.service import ConsentRecordService, ConsentTemplateService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


# ─── Templates ───────────────────────────────────────────────────────────


@router.get("/consent-templates", response_model=list[s.ConsentTemplateRead])
async def list_templates(
    include_drafts: bool = False, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))
):
    # Drafts are the super admin's work in progress — nobody else sees them.
    return await ConsentTemplateService(db).list(include_drafts=include_drafts and ctx.role == "super_admin")


@router.post("/consent-templates", response_model=s.ConsentTemplateRead, status_code=201)
async def create_template(body: s.ConsentTemplateCreate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))):
    """New draft; version = latest for (type, role, language) + 1."""
    return await ConsentTemplateService(db).create_draft(body.model_dump(mode="json"))


@router.patch("/consent-templates/{template_id}", response_model=s.ConsentTemplateRead)
async def update_template(
    template_id: UUID, body: s.ConsentTemplateUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))
):
    return await ConsentTemplateService(db).update_draft(template_id, body.model_dump(mode="json", exclude_none=True))


@router.delete("/consent-templates/{template_id}", status_code=204)
async def delete_template(template_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))):
    await ConsentTemplateService(db).delete_draft(template_id)
    return Response(status_code=204)


@router.post("/consent-templates/{template_id}/publish")
async def publish_template(
    template_id: UUID, body: s.ConsentTemplatePublish, db=Depends(get_db), _ctx: RequestContext = Depends(require_role("super_admin"))
) -> dict:
    """Freezes the draft and makes it the active version (the previous active
    version of the same type/role/language is retired)."""
    published = await ConsentTemplateService(db).publish(template_id, require_reconsent=body.require_reconsent)
    return {**s.ConsentTemplateRead(**published).model_dump(mode="json"), "reconsent_count": published["reconsent_count"]}


# ─── Records ─────────────────────────────────────────────────────────────


@router.post("/consent-records", response_model=s.ConsentRecordRead, status_code=201)
async def create_consent_record(
    body: s.ConsentRecordCreate,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role("super_admin", "regional_admin", "clinic_admin", "receptionist")),
):
    """Creates a PENDING consent for a patient or staff member (exit,
    transfer, ...). Only that person can sign it."""
    if not body.patient_id and not body.staff_id:
        raise BusinessRuleError("Either patient_id or staff_id must be set", code="CONSENT_SUBJECT_REQUIRED")
    record = await ConsentRecordService(db).create(
        consent_type=body.consent_type,
        patient_id=body.patient_id,
        staff_id=body.staff_id,
        clinic_id=body.clinic_id,
        region_id=body.region_id,
        role=body.role,
    )
    return s.ConsentRecordRead.from_row(record)


@router.get("/consent-records/me/pending", response_model=s.ConsentDocument | None)
async def my_pending_consent(
    language: str = Query(default="en", pattern=r"^[a-z]{2}$"),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """The caller's next consent to sign, rendered exactly as it must be
    shown. null when nothing is pending."""
    return await ConsentRecordService(db).my_pending(ctx, language)


@router.get("/consent-records", response_model=list[s.ConsentRecordRead])
async def list_consent_records(
    patient_id: UUID | None = None,
    staff_id: UUID | None = None,
    clinic_id: UUID | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    # A patient is forced to their own records at the app layer, on top of RLS.
    if ctx.role == "patient":
        patient_id, staff_id, clinic_id = UUID(ctx.user_id), None, None
    rows = await ConsentRecordService(db).list(patient_id=patient_id, staff_id=staff_id, clinic_id=clinic_id)
    return [s.ConsentRecordRead.from_row(r) for r in rows]


async def _visible_record(consent_id: UUID, db, ctx: RequestContext) -> dict:
    record = await ConsentRecordService(db).get(consent_id)
    assert_owns_profile(ctx, record["patient_id"])
    return record


@router.get("/consent-records/{consent_id}", response_model=s.ConsentRecordRead)
async def get_consent_record(consent_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    return s.ConsentRecordRead.from_row(await _visible_record(consent_id, db, ctx))


@router.get("/consent-records/{consent_id}/document", response_model=s.ConsentDocument)
async def get_consent_document(consent_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    """Signed: the exact text, ticks and signer stored at signing.
    Pending: the text as it would be shown now."""
    return await ConsentRecordService(db).document(await _visible_record(consent_id, db, ctx))


@router.get("/consent-records/{consent_id}/pdf", response_model=s.PdfLink)
async def get_consent_pdf(consent_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    """Short-lived download link for the signed PDF (built now if it was
    never stored)."""
    return await ConsentRecordService(db).pdf_link(await _visible_record(consent_id, db, ctx))


@router.patch("/consent-records/{consent_id}/status", response_model=s.ConsentRecordRead)
async def update_consent_status(
    consent_id: UUID,
    body: s.ConsentStatusUpdate,
    request: Request,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Sign or withdraw — only by the person the consent belongs to."""
    service = ConsentRecordService(db)
    if body.status == "signed":
        record = await service.sign(
            consent_id,
            ctx=ctx,
            accepted=body.accepted,
            rendered_hash=body.rendered_hash,
            user_agent=request.headers.get("user-agent"),
        )
    else:
        record = await service.revoke(consent_id, ctx=ctx)
    return s.ConsentRecordRead.from_row(record)
