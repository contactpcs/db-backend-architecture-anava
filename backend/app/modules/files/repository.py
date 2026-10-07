from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class EegFileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        patient_id: UUID,
        clinic_id: UUID,
        performed_by: UUID,
        eeg_type: str | None,
        duration_minutes,
        raw_data_s3_key: str,
        raw_file_name: str,
        raw_file_size: int,
        raw_checksum: str,
        raw_s3_version_id: str | None,
    ) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO patient_eeg_files (patient_id, clinic_id, performed_by, eeg_type, duration_minutes, "
                "raw_data_s3_key, raw_file_name, raw_file_size, raw_checksum, raw_s3_version_id) VALUES "
                "(:patient_id, :clinic_id, :performed_by, :eeg_type, :duration_minutes, :key, :name, :size, :checksum, :version_id) "
                "RETURNING *"
            ),
            {
                "patient_id": str(patient_id),
                "clinic_id": str(clinic_id),
                "performed_by": str(performed_by),
                "eeg_type": eeg_type or "resting_state",
                "duration_minutes": duration_minutes,
                "key": raw_data_s3_key,
                "name": raw_file_name,
                "size": raw_file_size,
                "checksum": raw_checksum,
                "version_id": raw_s3_version_id,
            },
        )

    async def get(self, eeg_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM patient_eeg_files WHERE eeg_id = :id"), {"id": str(eeg_id)})

    async def list_for_patient(self, patient_id: UUID) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM patient_eeg_files WHERE patient_id = :pid ORDER BY created_at DESC"),
                    {"pid": str(patient_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def review(self, eeg_id: UUID, *, reviewed_by: UUID, clinical_findings, is_abnormal, status: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patient_eeg_files SET reviewed_by = :reviewed_by, clinical_findings = :findings, "
                "is_abnormal = :abnormal, status = :status, reviewed_at = NOW() WHERE eeg_id = :id RETURNING *"
            ),
            {
                "reviewed_by": str(reviewed_by),
                "findings": clinical_findings,
                "abnormal": is_abnormal,
                "status": status,
                "id": str(eeg_id),
            },
        )


class MedicalHistoryFileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        patient_id: UUID,
        clinic_id: UUID,
        uploaded_by: UUID,
        document_type: str | None,
        s3_key: str,
        file_name: str,
        file_size: int | None,
        checksum: str | None,
        document_date,
        source_provider,
        description,
        mime_type: str | None = None,
        s3_version_id: str | None = None,
        status: str = "verified",
    ) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO patient_medical_history_files (patient_id, clinic_id, uploaded_by, document_type, "
                "s3_key, file_name, file_size, checksum, document_date, source_provider, description, "
                "mime_type, s3_version_id, status) VALUES "
                "(:patient_id, :clinic_id, :uploaded_by, :doc_type, :key, :name, :size, :checksum, :doc_date, :source, :description, "
                ":mime_type, :version_id, :status) "
                "RETURNING *"
            ),
            {
                "patient_id": str(patient_id),
                "clinic_id": str(clinic_id),
                "uploaded_by": str(uploaded_by),
                "doc_type": document_type or "other",
                "key": s3_key,
                "name": file_name,
                "size": file_size,
                "checksum": checksum,
                "doc_date": document_date,
                "source": source_provider,
                "description": description,
                "mime_type": mime_type,
                "version_id": s3_version_id,
                "status": status,
            },
        )

    async def get(self, mhf_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM patient_medical_history_files WHERE mhf_id = :id"),
            {"id": str(mhf_id)},
        )

    async def list_for_patient(self, patient_id: UUID, *, include_unpromoted: bool = False) -> list[dict]:
        """include_unpromoted adds the rows still being scanned and the
        rejected ones — files that are not in the patient records bucket."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM patient_medical_history_files WHERE patient_id = :pid "
                        "AND is_deleted = FALSE AND (:all OR status IN ('unverified', 'verified')) "
                        "ORDER BY created_at DESC"
                    ),
                    {"pid": str(patient_id), "all": include_unpromoted},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def soft_delete(self, mhf_id: UUID, *, deleted_by: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patient_medical_history_files SET is_deleted = TRUE, deleted_by = :by, "
                "deleted_at = NOW() WHERE mhf_id = :id RETURNING *"
            ),
            {"by": str(deleted_by), "id": str(mhf_id)},
        )

    async def get_for_update(self, mhf_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM patient_medical_history_files WHERE mhf_id = :id FOR UPDATE"),
            {"id": str(mhf_id)},
        )

    async def finish_scan(
        self,
        mhf_id: UUID,
        *,
        status: str,
        scan_result: str,
        file_size: int | None = None,
        checksum: str | None = None,
        s3_version_id: str | None = None,
    ) -> dict | None:
        """Moves a row out of 'scanning'. Matches nothing if it already left,
        so a repeated scan event cannot change a decided row."""
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patient_medical_history_files SET status = :status, scan_result = :scan_result, "
                "file_size = :size, checksum = :checksum, s3_version_id = :version_id "
                "WHERE mhf_id = :id AND status = 'scanning' RETURNING *"
            ),
            {
                "status": status,
                "scan_result": scan_result,
                "size": file_size,
                "checksum": checksum,
                "version_id": s3_version_id,
                "id": str(mhf_id),
            },
        )

    async def verify(self, mhf_id: UUID, *, verified_by: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE patient_medical_history_files SET status = 'verified', verified_by = :by, verified_at = NOW() "
                "WHERE mhf_id = :id AND status = 'unverified' RETURNING *"
            ),
            {"by": str(verified_by), "id": str(mhf_id)},
        )

    async def scanning_ids(self) -> list[UUID]:
        rows = await self.session.execute(text("SELECT mhf_id FROM patient_medical_history_files WHERE status = 'scanning'"))
        return [r[0] for r in rows]


async def clinic_region_id(session: AsyncSession, clinic_id: UUID) -> UUID | None:
    return (await session.execute(text("SELECT region_id FROM clinics WHERE clinic_id = :id"), {"id": str(clinic_id)})).scalar()
