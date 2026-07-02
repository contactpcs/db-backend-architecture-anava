from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import NotFoundError
from app.integrations import s3
from app.modules.files.repository import EegFileRepository, MedicalHistoryFileRepository


async def _resolve_profile_id(session: AsyncSession, patient_id: UUID) -> UUID:
    """Same fix as anamnesis/prs/consent (Stage 6) — file tables reference
    profiles(id), API accepts patients.patient_id."""
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(session).get(patient_id)
    if not patient:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
    return patient["profile_id"]


class FileService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.eeg = EegFileRepository(session)
        self.mhf = MedicalHistoryFileRepository(session)

    async def presign_upload(self, patient_id: UUID, *, doc_type: str, file_name: str, clinic_id: UUID) -> dict:
        key = s3.build_key(clinic_id=str(clinic_id), patient_id=str(patient_id), category=doc_type, filename=file_name)
        return {"s3_key": key, "upload_url": s3.presign_upload(key)}

    async def upload_bytes(self, s3_key: str, content: bytes) -> dict:
        size, checksum = s3.save_bytes(s3_key, content)
        return {"size": size, "checksum": checksum}

    async def confirm(self, patient_id: UUID, data: dict, *, uploaded_by: UUID) -> dict:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        if not s3.exists(data["s3_key"]):
            raise NotFoundError(f"No uploaded file found at key {data['s3_key']!r} — call the upload step first", code="FILE_NOT_UPLOADED")
        content = s3.read_bytes(data["s3_key"])
        size = len(content)

        if data["doc_type"] == "eeg":
            record = await self.eeg.create(
                patient_id=profile_id, clinic_id=data["clinic_id"], performed_by=uploaded_by,
                eeg_type=data.get("eeg_type"), duration_minutes=data.get("duration_minutes"),
                raw_data_s3_key=data["s3_key"], raw_file_name=data["file_name"], raw_file_size=size,
                raw_checksum=_sha256(content),
            )
            event_type = "eeg_uploaded"
            file_id_key = "eeg_id"
        else:
            record = await self.mhf.create(
                patient_id=profile_id, clinic_id=data["clinic_id"], uploaded_by=uploaded_by,
                document_type=data.get("document_type"), s3_key=data["s3_key"], file_name=data["file_name"],
                file_size=size, checksum=_sha256(content), document_date=data.get("document_date"),
                source_provider=data.get("source_provider"), description=data.get("description"),
            )
            event_type = "file_uploaded"
            file_id_key = "mhf_id"

        await emit_event(
            self.session, aggregate_type="patient_file", aggregate_id=record[file_id_key],
            event_type=event_type, payload={"file_id": str(record[file_id_key]), "patient_id": str(patient_id)},
        )
        return {**record, "doc_type": data["doc_type"], "file_id": record[file_id_key]}

    async def list_for_patient(self, patient_id: UUID, *, doc_type: str | None = None) -> list[dict]:
        profile_id = await _resolve_profile_id(self.session, patient_id)
        results = []
        if doc_type in (None, "eeg"):
            for r in await self.eeg.list_for_patient(profile_id):
                results.append({**r, "doc_type": "eeg", "file_id": r["eeg_id"], "file_name": r["raw_file_name"], "file_size": r["raw_file_size"], "checksum": r["raw_checksum"]})
        if doc_type in (None, "medical_history"):
            for r in await self.mhf.list_for_patient(profile_id):
                results.append({**r, "doc_type": "medical_history", "file_id": r["mhf_id"]})
        return results

    async def download_url(self, doc_type: str, file_id: UUID) -> str:
        record = await self.eeg.get(file_id) if doc_type == "eeg" else await self.mhf.get(file_id)
        if not record:
            raise NotFoundError("File not found", code="FILE_NOT_FOUND")
        key = record["raw_data_s3_key"] if doc_type == "eeg" else record["s3_key"]
        return s3.presign_download(key)

    async def review_eeg(self, eeg_id: UUID, *, reviewed_by: UUID, clinical_findings, is_abnormal, status: str) -> dict:
        updated = await self.eeg.review(eeg_id, reviewed_by=reviewed_by, clinical_findings=clinical_findings, is_abnormal=is_abnormal, status=status or "reviewed")
        if not updated:
            raise NotFoundError("EEG file not found", code="FILE_NOT_FOUND")
        await emit_event(
            self.session, aggregate_type="patient_file", aggregate_id=eeg_id,
            event_type="eeg_reviewed", payload={"eeg_id": str(eeg_id), "is_abnormal": is_abnormal},
        )
        return updated


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()
