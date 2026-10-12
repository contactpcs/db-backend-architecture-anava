from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from app.config import get_settings
from app.core.db import RequestContext, get_db
from app.core.exceptions import NotFoundError, PermissionError_
from app.core.permissions import require_role
from app.core.scoping import assert_patient_self
from app.integrations import s3
from app.modules.files import schemas as s
from app.modules.files.repository import MedicalHistoryFileRepository
from app.modules.files.service import FileService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


def _require_own_medical_history(ctx: RequestContext, doc_type: str) -> None:
    """A patient uploading their own records may only ever create
    medical_history docs (not eeg — that's clinical data staff record)."""
    if ctx.role == "patient" and doc_type != "medical_history":
        raise PermissionError_("Patients may only upload medical history documents", code="DOC_TYPE_NOT_ALLOWED")


def _require_local_storage() -> None:
    """The two raw-bytes routes below exist only for local mode. In S3 mode
    the browser talks to S3 directly, and these would be a way around the
    quarantine bucket and the size / content-type limits."""
    if get_settings().file_storage_mode == "s3":
        raise NotFoundError("Not found", code="NOT_FOUND")


async def _assert_own_key(ctx: RequestContext, db, s3_key: str) -> None:
    """Ownership check for the two raw-bytes local-dev routes below, which
    (unlike the other endpoints) only ever see a key, not a patient_id path
    param to run assert_patient_self against directly. A records key embeds
    the patient_id segment (see s3.build_key); a quarantine key
    (incoming/{upload_id}) names the row instead."""
    if ctx.role != "patient":
        return
    if s3_key.startswith("incoming/"):
        row = await MedicalHistoryFileRepository(db).get(UUID(s3_key.removeprefix("incoming/")))
        if not row or str(row["patient_id"]) != ctx.user_id:
            raise PermissionError_("Not your file", code="FORBIDDEN")
        return
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(db).get_by_profile_id(UUID(ctx.user_id))
    own_patient_id = str(patient["patient_id"]) if patient else None
    if not own_patient_id or f"/patients/{own_patient_id}/" not in s3_key:
        raise PermissionError_("Not your file", code="FORBIDDEN")


@router.post("/patients/{patient_id}/files/presign-upload", response_model=s.PresignUploadResponse)
async def presign_upload(
    patient_id: UUID,
    body: s.PresignUploadRequest,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    await assert_patient_self(ctx, db, patient_id)
    _require_own_medical_history(ctx, body.doc_type)
    return await FileService(db).presign_upload(
        patient_id, body.model_dump(), uploaded_by=UUID(ctx.user_id), via_quarantine=ctx.role == "patient"
    )


@router.put("/files/upload/{s3_key:path}")
async def upload_file_bytes(
    s3_key: str,
    request: Request,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    """Local-dev stand-in for a real presigned S3 POST — the client uploads
    bytes directly here instead of to S3. Not served in S3 mode."""
    _require_local_storage()
    await _assert_own_key(ctx, db, s3_key)
    content = await request.body()
    return await FileService(db).upload_bytes(s3_key, content)


@router.get("/files/download/{s3_key:path}")
async def download_file_bytes(s3_key: str, db=Depends(get_db), ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient"))):
    _require_local_storage()
    await _assert_own_key(ctx, db, s3_key)
    path = s3.local_path(s3_key)
    # filename= makes it an attachment, same as the presigned S3 download.
    return FileResponse(path, filename=path.name)


@router.post("/patients/{patient_id}/files", status_code=201)
async def confirm_file_upload(
    patient_id: UUID,
    body: s.FileConfirmCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    """Staff only. A patient upload has no confirm step: its row is created
    by presign-upload and decided by the malware scan."""
    data = body.model_dump()
    data["clinic_id"] = str(data["clinic_id"])
    return await FileService(db).confirm(patient_id, data, uploaded_by=UUID(ctx.user_id))


@router.get("/patients/{patient_id}/files")
async def list_patient_files(
    patient_id: UUID,
    doc_type: str | None = None,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    await assert_patient_self(ctx, db, patient_id)
    # Only files that are in the patient records bucket are listed, to the
    # patient and to staff alike. An upload still being scanned, or one that
    # was rejected, is not shown to anyone.
    return await FileService(db).list_for_patient(patient_id, doc_type=doc_type)


@router.get("/files/{doc_type}/{file_id}/download-url")
async def get_download_url(
    doc_type: str,
    file_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF, "patient")),
):
    caller_profile_id = UUID(ctx.user_id) if ctx.role == "patient" else None
    return {"download_url": await FileService(db).download_url(doc_type, file_id, caller_profile_id=caller_profile_id)}


@router.patch("/files/medical_history/{file_id}/verify")
async def verify_patient_upload(
    file_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "doctor", "clinical_assistant")),
):
    """A clinician confirms a file the patient uploaded themselves."""
    return await FileService(db).verify(file_id, verified_by=UUID(ctx.user_id))


@router.patch("/eeg-files/{eeg_id}/review")
async def review_eeg_file(
    eeg_id: UUID,
    body: s.FileReviewUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role("super_admin", "doctor")),
):
    return await FileService(db).review_eeg(
        eeg_id,
        reviewed_by=UUID(ctx.user_id),
        clinical_findings=body.clinical_findings,
        is_abnormal=body.is_abnormal,
        status=body.status,
    )
