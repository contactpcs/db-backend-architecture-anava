from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import as_system
from app.core.events import emit_event
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations import s3
from app.modules.files.repository import EegFileRepository, MedicalHistoryFileRepository, clinic_region_id
from app.modules.patients.repository import PatientRepository

logger = structlog.get_logger()

# A 'scanning' row whose bytes never reached the quarantine bucket is given up
# on after this long. The presigned upload is only valid for 5 minutes.
UPLOAD_ABANDONED_AFTER = timedelta(hours=1)


class FileService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.eeg = EegFileRepository(session)
        self.mhf = MedicalHistoryFileRepository(session)

    async def _patient(self, patient_id: UUID) -> dict:
        patient = await PatientRepository(self.session).get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
        return patient

    async def _records_key(self, patient_id: UUID, clinic_id: UUID, *, category: str, file_name: str) -> str:
        region_id = await clinic_region_id(self.session, clinic_id)
        if not region_id:
            raise NotFoundError("Clinic not found", code="CLINIC_NOT_FOUND")
        return s3.build_key(
            "records",
            region_id=str(region_id),
            clinic_id=str(clinic_id),
            patient_id=str(patient_id),
            category=category,
            filename=file_name,
        )

    async def presign_upload(self, patient_id: UUID, data: dict, *, uploaded_by: UUID, via_quarantine: bool) -> dict:
        """Staff upload straight into the patient records bucket and then call
        confirm(). A file a patient uploads is untrusted (via_quarantine): the
        row is created here as 'scanning', the bytes go to the quarantine
        bucket, and promote_upload() decides what happens next — there is no
        confirm step for the client to call."""
        if not via_quarantine:
            key = await self._records_key(patient_id, data["clinic_id"], category=data["doc_type"], file_name=data["file_name"])
            return {"s3_key": key, "file": None, **s3.presign_upload(key, content_type=data["content_type"])}

        patient = await self._patient(patient_id)
        # The clinic comes from the patient record, not from the request.
        clinic_id = patient["primary_clinic_id"]
        record = await self.mhf.create(
            patient_id=patient["profile_id"],
            clinic_id=clinic_id,
            uploaded_by=uploaded_by,
            document_type=data.get("document_type"),
            # Where the file WILL be once it passes the scan; nothing is there yet.
            s3_key=await self._records_key(patient_id, clinic_id, category="patient_upload", file_name=data["file_name"]),
            file_name=data["file_name"],
            file_size=None,
            checksum=None,
            document_date=data.get("document_date"),
            source_provider=data.get("source_provider"),
            description=data.get("description"),
            mime_type=data["content_type"],
            status="scanning",
        )
        key = s3.build_key("quarantine", upload_id=str(record["mhf_id"]))
        return {
            "s3_key": key,
            "file": {**record, "doc_type": "medical_history", "file_id": record["mhf_id"]},
            **s3.presign_upload(key, content_type=data["content_type"], bucket="quarantine"),
        }

    async def upload_bytes(self, s3_key: str, content: bytes) -> dict:
        """Local-mode stand-in for the browser's upload to S3. There is no
        scanner locally, so an upload to a quarantine key is promoted at once."""
        if len(content) > s3.settings.upload_max_bytes:
            raise ValidationError("File is too large", code="FILE_TOO_LARGE")
        size, checksum, _ = s3.save_bytes(s3_key, content)
        if s3_key.startswith("incoming/"):
            await self.promote_upload(UUID(s3_key.removeprefix("incoming/")))
        return {"size": size, "checksum": checksum}

    async def confirm(self, patient_id: UUID, data: dict, *, uploaded_by: UUID) -> dict:
        profile_id = (await self._patient(patient_id))["profile_id"]
        # The key comes back from the client: it must be one presign_upload()
        # issued for this patient and this kind of document.
        if f"/patients/{patient_id}/{data['doc_type']}/" not in data["s3_key"]:
            raise ValidationError("s3_key does not belong to this patient", code="INVALID_FILE_KEY")
        meta = s3.head(data["s3_key"])
        if not meta:
            raise NotFoundError(f"No uploaded file found at key {data['s3_key']!r} — call the upload step first", code="FILE_NOT_UPLOADED")
        content = s3.read_bytes(data["s3_key"], version_id=meta["version_id"])
        size = len(content)

        if data["doc_type"] == "eeg":
            record = await self.eeg.create(
                patient_id=profile_id,
                clinic_id=data["clinic_id"],
                performed_by=uploaded_by,
                eeg_type=data.get("eeg_type"),
                duration_minutes=data.get("duration_minutes"),
                raw_data_s3_key=data["s3_key"],
                raw_file_name=data["file_name"],
                raw_file_size=size,
                raw_checksum=_sha256(content),
                raw_s3_version_id=meta["version_id"],
            )
            event_type = "eeg_uploaded"
            file_id_key = "eeg_id"
        else:
            record = await self.mhf.create(
                patient_id=profile_id,
                clinic_id=data["clinic_id"],
                uploaded_by=uploaded_by,
                document_type=data.get("document_type"),
                s3_key=data["s3_key"],
                file_name=data["file_name"],
                file_size=size,
                checksum=_sha256(content),
                document_date=data.get("document_date"),
                source_provider=data.get("source_provider"),
                description=data.get("description"),
                s3_version_id=meta["version_id"],
            )
            event_type = "file_uploaded"
            file_id_key = "mhf_id"

        await emit_event(
            self.session,
            aggregate_type="patient_file",
            aggregate_id=record[file_id_key],
            event_type=event_type,
            payload={"file_id": str(record[file_id_key]), "patient_id": str(patient_id)},
        )
        return {**record, "doc_type": data["doc_type"], "file_id": record[file_id_key]}

    async def promote_upload(self, upload_id: UUID) -> str:
        """Decides a patient upload once its scan result is known, and returns
        the row's status. Safe to run any number of times for the same upload:
        the row is locked, and only a row still 'scanning' is ever changed.

            NO_THREATS_FOUND   copied to the patient records bucket -> 'unverified'
            no scan tag yet    left 'scanning'
            never uploaded     left 'scanning' until UPLOAD_ABANDONED_AFTER, then 'rejected'
            anything else      'rejected' + alert (THREATS_FOUND, UNSUPPORTED,
                               ACCESS_DENIED, FAILED, or a value not seen before)

        Runs as RLS role 'system': the caller is the promotion job, or (local
        mode) the uploading patient, and neither may update the row itself."""
        async with as_system(self.session):
            row = await self.mhf.get_for_update(upload_id)
            if not row:
                raise NotFoundError("Upload not found", code="FILE_NOT_FOUND")
            if row["status"] != "scanning":
                return row["status"]

            result = s3.promote_if_clean(str(upload_id), row["s3_key"])
            scan = result["scan"]
            if scan == s3.NOT_SCANNED:
                return "scanning"
            if scan == s3.NOT_UPLOADED and datetime.now(UTC) - row["created_at"] < UPLOAD_ABANDONED_AFTER:
                return "scanning"

            status = "unverified" if scan == s3.SCAN_CLEAN else "rejected"
            await self.mhf.finish_scan(
                upload_id,
                status=status,
                scan_result=scan,
                file_size=result.get("size"),
                checksum=result.get("checksum"),
                s3_version_id=result.get("version_id"),
            )
            if status == "rejected" and scan != s3.NOT_UPLOADED:
                # The alert: alarm on this log line (CloudWatch metric filter).
                logger.error("patient_upload_rejected", upload_id=str(upload_id), scan_result=scan, clinic_id=str(row["clinic_id"]))
            await emit_event(
                self.session,
                aggregate_type="patient_file",
                aggregate_id=upload_id,
                event_type="file_uploaded" if status == "unverified" else "file_upload_rejected",
                payload={"file_id": str(upload_id), "scan_result": scan},
            )
            return status

    async def verify(self, file_id: UUID, *, verified_by: UUID) -> dict:
        """A clinician has opened a patient-uploaded file and confirms it."""
        updated = await self.mhf.verify(file_id, verified_by=verified_by)
        if not updated:
            if not await self.mhf.get(file_id):
                raise NotFoundError("File not found", code="FILE_NOT_FOUND")
            raise ConflictError("Only an unverified file can be verified", code="FILE_NOT_UNVERIFIED")
        await emit_event(
            self.session,
            aggregate_type="patient_file",
            aggregate_id=file_id,
            event_type="file_verified",
            payload={"file_id": str(file_id)},
        )
        return {**updated, "doc_type": "medical_history", "file_id": updated["mhf_id"]}

    async def list_for_patient(self, patient_id: UUID, *, doc_type: str | None = None, include_unpromoted: bool = False) -> list[dict]:
        profile_id = (await self._patient(patient_id))["profile_id"]
        results = []
        if doc_type in (None, "eeg"):
            for r in await self.eeg.list_for_patient(profile_id):
                results.append(
                    {
                        **r,
                        "doc_type": "eeg",
                        "file_id": r["eeg_id"],
                        "file_name": r["raw_file_name"],
                        "file_size": r["raw_file_size"],
                        "checksum": r["raw_checksum"],
                    }
                )
        if doc_type in (None, "medical_history"):
            for r in await self.mhf.list_for_patient(profile_id, include_unpromoted=include_unpromoted):
                results.append({**r, "doc_type": "medical_history", "file_id": r["mhf_id"]})
        return results

    async def download_url(self, doc_type: str, file_id: UUID, *, caller_profile_id: UUID | None = None) -> str:
        record = await self.eeg.get(file_id) if doc_type == "eeg" else await self.mhf.get(file_id)
        if not record:
            raise NotFoundError("File not found", code="FILE_NOT_FOUND")
        if caller_profile_id is not None and record["patient_id"] != caller_profile_id:
            raise NotFoundError("File not found", code="FILE_NOT_FOUND")
        if doc_type == "eeg":
            return s3.presign_download(record["raw_data_s3_key"], filename=record["raw_file_name"], version_id=record["raw_s3_version_id"])
        # Still in quarantine, or rejected: nothing to hand out to anyone.
        if record["status"] not in ("unverified", "verified"):
            raise ConflictError(
                "This file is still being scanned" if record["status"] == "scanning" else "This file did not pass the malware scan",
                code="FILE_NOT_AVAILABLE",
            )
        return s3.presign_download(record["s3_key"], filename=record["file_name"], version_id=record["s3_version_id"])

    async def review_eeg(self, eeg_id: UUID, *, reviewed_by: UUID, clinical_findings, is_abnormal, status: str | None) -> dict:
        updated = await self.eeg.review(
            eeg_id,
            reviewed_by=reviewed_by,
            clinical_findings=clinical_findings,
            is_abnormal=is_abnormal,
            status=status or "reviewed",
        )
        if not updated:
            raise NotFoundError("EEG file not found", code="FILE_NOT_FOUND")
        await emit_event(
            self.session,
            aggregate_type="patient_file",
            aggregate_id=eeg_id,
            event_type="eeg_reviewed",
            payload={"eeg_id": str(eeg_id), "is_abnormal": is_abnormal},
        )
        return updated


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()
