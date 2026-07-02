from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.modules.consent.repository import ConsentRecordRepository, ConsentTemplateRepository

# Master Doc Section 11.1 — only patient_onboarding requires a witness.
_WITNESS_REQUIRED_TYPES = {"patient_onboarding"}


class ConsentTemplateService:
    def __init__(self, session: AsyncSession):
        self.repo = ConsentTemplateRepository(session)

    async def list(self) -> list[dict]:
        return await self.repo.list()


class ConsentRecordService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ConsentRecordRepository(session)
        self.templates = ConsentTemplateRepository(session)

    async def create(self, *, consent_type: str, patient_id, staff_id, clinic_id: UUID) -> dict:
        # consent_records.patient_id references profiles(id), not
        # patients.patient_id — same distinction as anamnesis/prs (Stage 6
        # fix). staff_id has no equivalent unified "staff.staff_id" concept
        # in this schema (each role has its own PK), so it stays a direct
        # profiles(id) reference — that's the correct design there, not an
        # inconsistency.
        if patient_id:
            from app.modules.patients.repository import PatientRepository

            patient = await PatientRepository(self.session).get(patient_id)
            if not patient:
                raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
            patient_id = patient["profile_id"]
        template = await self.templates.get_active(consent_type)
        if not template:
            raise NotFoundError(f"No active template for consent_type={consent_type!r}", code="CONSENT_TEMPLATE_NOT_FOUND")
        record = await self.repo.create(
            consent_type=consent_type, template_id=template["template_id"],
            patient_id=patient_id, staff_id=staff_id, clinic_id=clinic_id,
        )
        await emit_event(
            self.session, aggregate_type="consent_record", aggregate_id=record["consent_id"],
            event_type="consent_generated", payload={"consent_id": str(record["consent_id"]), "consent_type": consent_type},
        )
        return record

    async def get(self, consent_id: UUID) -> dict:
        record = await self.repo.get(consent_id)
        if not record:
            raise NotFoundError("Consent record not found", code="CONSENT_NOT_FOUND")
        return record

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def sign(self, consent_id: UUID, *, signed_by: UUID, witness_id, signature_data: str, ip_address) -> dict:
        record = await self.get(consent_id)
        if record["status"] != "pending":
            raise BusinessRuleError(f"Consent already {record['status']}", code="CONSENT_ALREADY_DECIDED")
        if record["consent_type"] in _WITNESS_REQUIRED_TYPES and not witness_id:
            raise BusinessRuleError(
                f"consent_type={record['consent_type']!r} requires a witness_id at signing",
                code="WITNESS_REQUIRED",
            )
        template = await self.templates.get_active(record["consent_type"])
        content_hash = template["content_hash"] if template else None
        updated = await self.repo.sign(
            consent_id, signed_by=signed_by, witness_id=witness_id,
            signature_data=signature_data, ip_address=ip_address, content_hash_at_signing=content_hash,
        )
        await emit_event(
            self.session, aggregate_type="consent_record", aggregate_id=consent_id,
            event_type="consent_signed", payload={"consent_id": str(consent_id), "consent_type": record["consent_type"]},
        )
        if record["consent_type"] == "patient_onboarding" and record["patient_id"]:
            # Local import — avoids a module-load-time circular import
            # (patients/service.py imports staff, not consent; this is the
            # only direction that needs to stay one-way).
            from app.modules.patients.repository import PatientRepository
            from app.modules.patients.service import PatientService

            patient = await PatientRepository(self.session).get_by_profile_id(record["patient_id"])
            if patient:
                await PatientService(self.session).advance_registration_status(patient["patient_id"])
        return updated  # type: ignore[return-value]

    async def revoke(self, consent_id: UUID, *, revoked_by: UUID) -> dict:
        record = await self.get(consent_id)
        if record["status"] != "signed":
            raise BusinessRuleError("Only a signed consent can be revoked", code="CONSENT_NOT_SIGNED")
        updated = await self.repo.revoke(consent_id, revoked_by=revoked_by)
        await emit_event(
            self.session, aggregate_type="consent_record", aggregate_id=consent_id,
            event_type="consent_revoked", payload={"consent_id": str(consent_id)},
        )
        return updated  # type: ignore[return-value]
