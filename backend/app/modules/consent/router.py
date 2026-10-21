from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.db import RequestContext, get_db
from app.core.exceptions import BusinessRuleError, PermissionError_
from app.core.permissions import require_role
from app.core.scoping import assert_owns_profile
from app.modules.consent import schemas as s
from app.modules.consent.service import ConsentRecordService, ConsentTemplateService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


def _active_super_admin(ctx: RequestContext = Depends(require_role("super_admin"))) -> RequestContext:
    """Consent routes stay reachable for an INACTIVE account (it has to sign
    its own onboarding consent), so the middleware lets them through. Writing
    or previewing the forms everyone signs needs an active super admin — not
    one still unsigned, nor one who was deactivated."""
    if not ctx.is_active:
        raise PermissionError_("Your account must be active to manage consent forms", code="ACCOUNT_INACTIVE")
    return ctx


# ─── Templates ───────────────────────────────────────────────────────────


@router.get("/consent-templates", response_model=list[s.ConsentTemplateRead])
async def list_templates(
    include_drafts: bool = False, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))
):
    # Drafts are the super admin's work in progress — nobody else sees them.
    return await ConsentTemplateService(db).list(include_drafts=include_drafts and ctx.role == "super_admin")


@router.get("/consent-templates/placeholders", response_model=list[s.Placeholder])
async def list_placeholders(
    consent_type: s.ConsentType, role: s.StaffRole | None = None, _ctx: RequestContext = Depends(_active_super_admin)
):
    """What the system fills in automatically for this consent type, and
    which of it a full form must / should contain."""
    return ConsentTemplateService.placeholders(consent_type, role)


@router.get("/consent-templates/starter", response_model=s.StarterForm)
async def starter_form(consent_type: s.ConsentType, role: s.StaffRole | None = None, _ctx: RequestContext = Depends(_active_super_admin)):
    """A ready-structured full form for this type: every field in place,
    placeholder wording to replace."""
    from app.modules.consent.starters import starter_html

    return {"filename": f"{consent_type}{'_' + role if role else ''}_starter.html", "html": starter_html(consent_type, role)}


@router.post("/consent-templates/analyze", response_model=s.FullFormAnalysis)
async def analyze_full_form(body: s.FullFormAnalyzeRequest, _ctx: RequestContext = Depends(_active_super_admin)):
    """Checks an uploaded full form before it is saved: the fields it will
    capture, missing required fields, unknown names, unsafe HTML."""
    from app.modules.consent.fill import analyze_full_html

    return analyze_full_html(body.consent_type, body.role, body.full_html)


# The preview is the admin's own HTML served from the API origin: forbid it
# from running anything even if opened directly in a tab.
_PREVIEW_CSP = "sandbox; default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'"


@router.post("/consent-templates/{template_id}/preview")
async def preview_template(
    template_id: UUID,
    format: Literal["html", "pdf"] = "html",
    signer: Literal["self", "guardian"] = "self",
    db=Depends(get_db),
    _ctx: RequestContext = Depends(_active_super_admin),
):
    """The form filled with sample details, exactly as a signed copy looks
    (format=pdf: the PDF that would be stored). Drafts included."""
    body, media_type = await ConsentTemplateService(db).preview(template_id, signer_type=signer, fmt=format)
    headers = {"Content-Security-Policy": _PREVIEW_CSP, "X-Content-Type-Options": "nosniff"}
    if format == "pdf":
        headers["Content-Disposition"] = 'inline; filename="consent_preview.pdf"'
    return Response(content=body, media_type=media_type, headers=headers)


@router.post("/consent-templates", response_model=s.ConsentTemplateRead, status_code=201)
async def create_template(body: s.ConsentTemplateCreate, db=Depends(get_db), _ctx: RequestContext = Depends(_active_super_admin)):
    """New draft; version = latest for (type, role, language) + 1."""
    return await ConsentTemplateService(db).create_draft(body.model_dump(mode="json"))


@router.patch("/consent-templates/{template_id}", response_model=s.ConsentTemplateRead)
async def update_template(
    template_id: UUID, body: s.ConsentTemplateUpdate, db=Depends(get_db), _ctx: RequestContext = Depends(_active_super_admin)
):
    return await ConsentTemplateService(db).update_draft(template_id, body.model_dump(mode="json", exclude_none=True))


@router.delete("/consent-templates/{template_id}", status_code=204)
async def delete_template(template_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(_active_super_admin)):
    await ConsentTemplateService(db).delete_draft(template_id)
    return Response(status_code=204)


@router.post("/consent-templates/{template_id}/publish")
async def publish_template(
    template_id: UUID, body: s.ConsentTemplatePublish, db=Depends(get_db), _ctx: RequestContext = Depends(_active_super_admin)
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


@router.get("/consent-records/{consent_id}/full", response_model=s.FullForm)
async def get_full_consent_form(consent_id: UUID, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    """The complete form behind "Read the full consent form", filled with the
    signer's details: as it reads now (pending) or exactly as signed. Show it
    in a sandboxed iframe (srcdoc)."""
    return await ConsentRecordService(db).full_form(await _visible_record(consent_id, db, ctx), ctx)


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
