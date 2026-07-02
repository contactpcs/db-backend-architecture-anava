from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class ConsentTemplateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active(self, consent_type: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT * FROM consent_templates WHERE consent_type = :t AND is_active = TRUE "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"t": consent_type},
        )

    async def list(self) -> list[dict]:
        rows = (await self.session.execute(text("SELECT * FROM consent_templates ORDER BY consent_type, version"))).mappings().all()
        return [dict(r) for r in rows]


class ConsentRecordRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, consent_type: str, template_id: UUID, patient_id, staff_id, clinic_id: UUID) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO consent_records (consent_type, template_id, patient_id, staff_id, clinic_id) "
                "VALUES (:consent_type, :template_id, :patient_id, :staff_id, :clinic_id) RETURNING *"
            ),
            {
                "consent_type": consent_type, "template_id": str(template_id),
                "patient_id": str(patient_id) if patient_id else None,
                "staff_id": str(staff_id) if staff_id else None,
                "clinic_id": str(clinic_id),
            },
        )

    async def get(self, consent_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM consent_records WHERE consent_id = :id"), {"id": str(consent_id)})

    async def list(self, *, patient_id: UUID | None = None, staff_id: UUID | None = None, clinic_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if patient_id:
            clauses.append("patient_id = :patient_id")
            params["patient_id"] = str(patient_id)
        if staff_id:
            clauses.append("staff_id = :staff_id")
            params["staff_id"] = str(staff_id)
        if clinic_id:
            clauses.append("clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM consent_records {where} ORDER BY created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def sign(self, consent_id: UUID, *, signed_by: UUID, witness_id, signature_data: str,
                    ip_address, content_hash_at_signing: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE consent_records SET status = 'signed', signed_at = NOW(), signed_by = :signed_by, "
                "witness_id = :witness_id, signature_data = :signature_data, ip_address = :ip_address, "
                "content_hash_at_signing = :hash WHERE consent_id = :id AND status = 'pending' RETURNING *"
            ),
            {
                "signed_by": str(signed_by), "witness_id": str(witness_id) if witness_id else None,
                "signature_data": signature_data, "ip_address": ip_address, "hash": content_hash_at_signing,
                "id": str(consent_id),
            },
        )

    async def revoke(self, consent_id: UUID, *, revoked_by: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE consent_records SET status = 'revoked', revoked_at = NOW(), revoked_by = :revoked_by "
                "WHERE consent_id = :id AND status = 'signed' RETURNING *"
            ),
            {"revoked_by": str(revoked_by), "id": str(consent_id)},
        )
