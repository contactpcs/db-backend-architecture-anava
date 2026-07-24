from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.modules.consent.repository import ConsentRecordRepository, ConsentTemplateRepository

_STAFF_ROLES = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


async def create_onboarding_consent(session: AsyncSession, *, role: str, profile_id, clinic_id=None, region_id=None) -> dict:
    """Single entry point for creating a new profile's onboarding consent
    record, used by every registration path (staff create, admin
    assign-admin, patient register) instead of each duplicating this logic
    slightly differently — that duplication is exactly how the regional_admin
    consent-creation gap happened."""
    consent_type = "staff_onboarding" if role in _STAFF_ROLES else "patient_onboarding"
    return await ConsentRecordService(session).create(
        consent_type=consent_type,
        patient_id=profile_id if consent_type == "patient_onboarding" else None,
        staff_id=profile_id if consent_type == "staff_onboarding" else None,
        clinic_id=clinic_id, region_id=region_id,
        role=role if consent_type == "staff_onboarding" else None,
    )


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

    async def create(self, *, consent_type: str, patient_id, staff_id, clinic_id: UUID | None = None,
                      region_id: UUID | None = None, role: str | None = None) -> dict:
        if clinic_id is None and region_id is None:
            raise BusinessRuleError("Either clinic_id or region_id must be set", code="CONSENT_SCOPE_REQUIRED")
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
        template = await self.templates.get_active(consent_type, role)
        if not template:
            raise NotFoundError(f"No active template for consent_type={consent_type!r} role={role!r}", code="CONSENT_TEMPLATE_NOT_FOUND")
        record = await self.repo.create(
            consent_type=consent_type, template_id=template["template_id"],
            patient_id=patient_id, staff_id=staff_id, clinic_id=clinic_id, region_id=region_id,
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
        if record["status"] == "signed":
            # Idempotent: a duplicate submit (double-click, network retry)
            # landing after the first one already succeeded should look like
            # success too, not a scary error — the desired end state (signed)
            # already holds. Only "revoked" is a genuine conflict worth
            # surfacing below.
            return record
        if record["status"] != "pending":
            raise BusinessRuleError(f"Consent already {record['status']}", code="CONSENT_ALREADY_DECIDED")

        # Hash the exact template this record was created against (via
        # template_id), not whatever's "currently active" for the type/role —
        # the active row can have moved on (new version, or now split by
        # role) by the time someone actually signs.
        template = await self.templates.get(record["template_id"])
        content_hash = template["content_hash"] if template else None
        updated = await self.repo.sign(
            consent_id, signed_by=signed_by, witness_id=witness_id,
            signature_data=signature_data, ip_address=ip_address, content_hash_at_signing=content_hash,
        )
        if updated is None:
            # Truly concurrent double-submit — both requests read status=
            # 'pending' above before either commit, but repo.sign()'s own
            # WHERE status='pending' means only one UPDATE actually matched.
            # The loser lands here instead of crashing on a None response.
            return await self.get(consent_id)
        await emit_event(
            self.session, aggregate_type="consent_record", aggregate_id=consent_id,
            event_type="consent_signed", payload={"consent_id": str(consent_id), "consent_type": record["consent_type"]},
        )
        # consent_signed is a plain "did they sign" flag, set for whichever
        # role signed — separate from is_active (see profiles.consent_signed,
        # SQL/28_consent_redesign.sql). is_active handling stays split by role
        # below since staff and patients activate on different triggers.
        signer_id = record["staff_id"] or record["patient_id"]
        await self.session.execute(
            text("UPDATE profiles SET consent_signed = TRUE WHERE id = :id"), {"id": str(signer_id)}
        )

        # staff_onboarding activates immediately — no registration-test flow
        # applies to staff. patient_onboarding does NOT activate here for
        # either self- or staff-registered patients anymore: every patient
        # must complete the full registration-test sequence (disease
        # selection, anamnesis, general PRS) first. Activation happens at
        # registration_complete (_complete_registration, auto for
        # approval_status='not_required') or at receptionist approval
        # (decide_approval, for self-registered) — never at consent sign.
        if record["consent_type"] == "staff_onboarding" and record["staff_id"]:
            await self.session.execute(
                text("UPDATE profiles SET is_active = TRUE WHERE id = :id"), {"id": str(record["staff_id"])}
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
