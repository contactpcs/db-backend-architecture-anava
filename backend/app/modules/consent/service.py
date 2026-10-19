from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionError_, ValidationError
from app.modules.consent.repository import ConsentRecordRepository, ConsentTemplateRepository

logger = structlog.get_logger()
settings = get_settings()

_STAFF_ROLES = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
_IST = ZoneInfo("Asia/Kolkata")


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
        clinic_id=clinic_id,
        region_id=region_id,
        role=role if consent_type == "staff_onboarding" else None,
    )


# ─── Rendering ───────────────────────────────────────────────────────────


def age_on(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def resolve_signer(subject: dict, today: date) -> dict:
    """Who signs: the patient, or their guardian when the patient is a minor
    (mandatory) or a guardian is on file (adults under assisted care). Staff
    always sign for themselves."""
    full_name = f"{subject['first_name']} {subject['last_name']}".strip()
    contact = subject.get("phone") or subject.get("email")
    if contact and contact.endswith("@no-email.local"):
        contact = None
    if subject["role"] != "patient":
        return {"signer_type": "self", "name": full_name, "contact": contact}
    age = age_on(subject.get("dob"), today)
    minor = age is not None and age < 18
    if subject.get("guardian_name"):
        return {
            "signer_type": "guardian",
            "name": subject["guardian_name"],
            "relation": subject.get("guardian_relationship"),
            "contact": subject.get("guardian_contact") or contact,
            "guardian_reason": "minor" if minor else subject.get("guardian_reason"),
            "on_behalf_of": full_name,
        }
    if minor:
        raise BusinessRuleError(
            "This patient is under 18 — a guardian must be added to the patient's record before consent can be given",
            code="GUARDIAN_REQUIRED",
        )
    return {"signer_type": "self", "name": full_name, "contact": contact}


def render(template: dict, subject: dict, signer: dict, clinic_name: str | None) -> dict:
    values = {
        "[PATIENT_NAME]": f"{subject['first_name']} {subject['last_name']}".strip(),
        "[SIGNER_NAME]": signer["name"],
        "[GUARDIAN_NAME]": signer["name"] if signer["signer_type"] == "guardian" else "",
        "[CLINIC_NAME]": clinic_name or "",
        "[ROLE]": subject["role"].replace("_", " ").title(),
    }

    def fill(s: str) -> str:
        for placeholder, value in values.items():
            if value:
                s = s.replace(placeholder, value)
        return s

    return {
        "title": fill(template["title"]),
        "content": fill(template["content"]),
        "clauses": [{"code": c["code"], "label": fill(c["label"]), "required": bool(c.get("required", True))} for c in template["clauses"]],
    }


def hash_rendered(rendered: dict) -> str:
    canonical = json.dumps(rendered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_responses(clauses: list[dict], accepted: list[str]) -> list[dict]:
    codes = {c["code"] for c in clauses}
    unknown = sorted(set(accepted) - codes)
    if unknown:
        raise ValidationError(f"Unknown consent clause(s): {', '.join(unknown)}", code="CONSENT_UNKNOWN_CLAUSE")
    missing = [c["code"] for c in clauses if c["required"] and c["code"] not in accepted]
    if missing:
        raise ValidationError("Every required statement must be ticked", code="CONSENT_REQUIRED_CLAUSE", details=[{"missing": missing}])
    return [{**c, "accepted": c["code"] in accepted} for c in clauses]


# ─── Templates (super admin) ─────────────────────────────────────────────


class ConsentTemplateService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ConsentTemplateRepository(session)

    async def list(self, *, include_drafts: bool = False) -> list[dict]:
        return await self.repo.list(include_drafts=include_drafts)

    async def _get(self, template_id: UUID) -> dict:
        template = await self.repo.get(template_id)
        if not template:
            raise NotFoundError("Consent template not found", code="CONSENT_TEMPLATE_NOT_FOUND")
        return template

    async def create_draft(self, data: dict) -> dict:
        if (data["consent_type"] == "staff_onboarding") != (data.get("role") is not None):
            raise ValidationError("role is required for staff_onboarding and not allowed for other types", code="CONSENT_TEMPLATE_ROLE")
        return await self.repo.create_draft(**data)

    async def update_draft(self, template_id: UUID, fields: dict) -> dict:
        template = await self._get(template_id)
        if template["published_at"] is not None:
            raise ConflictError("A published template cannot be changed — create a new version", code="CONSENT_TEMPLATE_PUBLISHED")
        if not fields:
            return template
        return await self.repo.update_draft(template_id, fields)  # type: ignore[return-value]

    async def delete_draft(self, template_id: UUID) -> None:
        template = await self._get(template_id)
        if template["published_at"] is not None:
            raise ConflictError("A published template cannot be deleted", code="CONSENT_TEMPLATE_PUBLISHED")
        await self.repo.delete_draft(template_id)

    async def publish(self, template_id: UUID, *, require_reconsent: bool) -> dict:
        template = await self._get(template_id)
        if template["published_at"] is not None:
            raise ConflictError("Template is already published", code="CONSENT_TEMPLATE_PUBLISHED")
        if not template["clauses"]:
            raise BusinessRuleError("Add at least one checkbox before publishing", code="CONSENT_TEMPLATE_NO_CLAUSES")
        if require_reconsent and template["consent_type"] != "patient_onboarding":
            raise ValidationError("Re-consent applies to patient_onboarding only", code="CONSENT_RECONSENT_TYPE")
        published = await self.repo.publish(template)
        reconsent_count = 0
        if require_reconsent:
            # Runs as the super admin, whose RLS sees every consent record and
            # may update every profile (rls_cr_select has no 'system' clause).
            reconsent_count = await ConsentRecordRepository(self.session).require_patient_reconsent(template_id)
        await emit_event(
            self.session,
            aggregate_type="consent_template",
            aggregate_id=template_id,
            event_type="consent_template_published",
            payload={"template_id": str(template_id), "consent_type": template["consent_type"], "reconsent_count": reconsent_count},
        )
        return {**published, "reconsent_count": reconsent_count}


# ─── Records ─────────────────────────────────────────────────────────────


class ConsentRecordService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ConsentRecordRepository(session)
        self.templates = ConsentTemplateRepository(session)

    async def create(
        self,
        *,
        consent_type: str,
        patient_id,
        staff_id,
        clinic_id: UUID | None = None,
        region_id: UUID | None = None,
        role: str | None = None,
    ) -> dict:
        if clinic_id is None and region_id is None:
            raise BusinessRuleError("Either clinic_id or region_id must be set", code="CONSENT_SCOPE_REQUIRED")
        # consent_records.patient_id references profiles(id), not
        # patients.patient_id — same distinction as anamnesis/prs. staff_id
        # is a direct profiles(id) reference.
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
            consent_type=consent_type,
            template_id=template["template_id"],
            patient_id=patient_id,
            staff_id=staff_id,
            clinic_id=clinic_id,
            region_id=region_id,
        )
        await emit_event(
            self.session,
            aggregate_type="consent_record",
            aggregate_id=record["consent_id"],
            event_type="consent_generated",
            payload={"consent_id": str(record["consent_id"]), "consent_type": consent_type},
            # Only fills a gap for the genuinely anonymous caller; no-op for
            # every authenticated caller, see emit_event()'s own guard.
            actor_role="patient" if consent_type == "patient_onboarding" else role,
        )
        return record

    async def get(self, consent_id: UUID) -> dict:
        record = await self.repo.get(consent_id)
        if not record:
            raise NotFoundError("Consent record not found", code="CONSENT_NOT_FOUND")
        return record

    async def list(self, **filters) -> list[dict]:
        return await self.repo.list(**filters)

    async def _subject(self, record: dict) -> dict:
        profile_id = record["patient_id"] or record["staff_id"]
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.id, p.role, p.first_name, p.last_name, p.dob, p.phone, p.email, "
                        "pt.guardian_name, pt.guardian_relationship, pt.guardian_contact, pt.guardian_reason "
                        "FROM profiles p LEFT JOIN patients pt ON pt.profile_id = p.id WHERE p.id = :id"
                    ),
                    {"id": str(profile_id)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("Consent subject not found", code="CONSENT_SUBJECT_NOT_FOUND")
        return dict(row)

    async def _clinic(self, clinic_id) -> dict | None:
        if not clinic_id:
            return None
        row = (
            (await self.session.execute(text("SELECT clinic_name, region_id FROM clinics WHERE clinic_id = :id"), {"id": str(clinic_id)}))
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def _render_pending(self, record: dict) -> tuple[dict, dict, dict]:
        template = await self.templates.get(record["template_id"])
        subject = await self._subject(record)
        signer = resolve_signer(subject, datetime.now(_IST).date())
        clinic = await self._clinic(record["clinic_id"])
        return template, signer, render(template, subject, signer, clinic["clinic_name"] if clinic else None)  # type: ignore[arg-type]

    async def document(self, record: dict) -> dict:
        template = await self.templates.get(record["template_id"])
        assert template is not None
        if record["status"] == "pending":
            template, signer, rendered = await self._render_pending(record)
            return {
                **record,
                "version": template["version"],
                "language": template["language"],
                "title": rendered["title"],
                "content": rendered["content"],
                "clauses": rendered["clauses"],
                "rendered_hash": hash_rendered(rendered),
                "signer": signer,
                "pdf_available": False,
            }
        # Signed/revoked: exactly what was stored at signing. Records signed
        # before v2 have no snapshot — fall back to their template text.
        signer = None
        if record.get("signer_type"):
            signer = {
                "signer_type": record["signer_type"],
                "name": record["signer_name"],
                "relation": record["signer_relation"],
                "contact": record["signer_contact"],
                "guardian_reason": record["guardian_reason"],
            }
        return {
            **record,
            "version": template["version"],
            "language": template["language"],
            "title": record.get("rendered_title") or template["title"],
            "content": record.get("rendered_content") or template["content"],
            "clauses": record.get("responses") or [],
            "rendered_hash": record.get("rendered_hash") or record.get("content_hash_at_signing") or "",
            "signer": signer,
            "pdf_available": bool(record.get("pdf_s3_key")),
        }

    async def my_pending(self, ctx: RequestContext, language: str = "en") -> dict | None:
        """The caller's oldest pending consent, moved onto the currently
        active template version (nothing has been signed on it yet), rendered
        for display. A patient gated for re-consent with no pending record
        gets one created."""
        record = await self.repo.oldest_pending_for(ctx.user_id)
        if record is None and ctx.role == "patient" and not ctx.consent_signed:
            clinic_id = (
                await self.session.execute(text("SELECT primary_clinic_id FROM patients WHERE profile_id = :id"), {"id": ctx.user_id})
            ).scalar()
            if clinic_id:
                record = await self.repo.create(
                    consent_type="patient_onboarding",
                    template_id=(await self._active_template_id("patient_onboarding", None, language)),
                    patient_id=ctx.user_id,
                    staff_id=None,
                    clinic_id=clinic_id,
                )
        if record is None:
            return None
        role = ctx.role if record["consent_type"] == "staff_onboarding" else None
        active = await self.templates.get_active(record["consent_type"], role, language)
        if active and active["template_id"] != record["template_id"]:
            record = await self.repo.set_template(record["consent_id"], active["template_id"])
        return await self.document(record)

    async def _active_template_id(self, consent_type: str, role: str | None, language: str):
        template = await self.templates.get_active(consent_type, role, language)
        if not template:
            raise NotFoundError(f"No active template for consent_type={consent_type!r}", code="CONSENT_TEMPLATE_NOT_FOUND")
        return template["template_id"]

    async def sign(
        self, consent_id: UUID, *, ctx: RequestContext, accepted: list[str], rendered_hash: str | None, user_agent: str | None
    ) -> dict:
        record = await self.get(consent_id)
        # Only the data principal (or the guardian holding the patient's
        # login) can consent. Staff never sign for a patient.
        if ctx.user_id not in (str(record["patient_id"] or ""), str(record["staff_id"] or "")):
            raise PermissionError_("Only the person this consent belongs to can sign it", code="CONSENT_SUBJECT_ONLY")
        if record["status"] == "signed":
            # Idempotent: a duplicate submit (double-click, network retry)
            # after the first one succeeded looks like success too.
            return record
        if record["status"] != "pending":
            raise BusinessRuleError(f"Consent already {record['status']}", code="CONSENT_ALREADY_DECIDED")

        template, signer, rendered = await self._render_pending(record)
        if not template["clauses"]:
            raise ConflictError("This consent form is out of date — reload it", code="CONSENT_TEXT_CHANGED")
        if rendered_hash != hash_rendered(rendered):
            raise ConflictError("The consent text changed since it was shown — reload and review it again", code="CONSENT_TEXT_CHANGED")
        responses = build_responses(rendered["clauses"], accepted)

        updated = await self.repo.sign(
            consent_id,
            {
                "signed_by": ctx.user_id,
                "rendered_title": rendered["title"],
                "rendered_content": rendered["content"],
                "rendered_hash": rendered_hash,
                "responses": responses,
                "content_hash_at_signing": template["content_hash"],
                "ip_address": ctx.ip_address,
                "user_agent": (user_agent or "")[:500] or None,
                "auth_method": f"{settings.auth_mode}_password_session",
                "signer_type": signer["signer_type"],
                "signer_name": signer["name"],
                "signer_relation": signer.get("relation"),
                "signer_contact": signer.get("contact"),
                "guardian_reason": signer.get("guardian_reason"),
            },
        )
        if updated is None:
            # Concurrent double-submit: repo.sign()'s WHERE status='pending'
            # let only one UPDATE through.
            return await self.get(consent_id)
        await emit_event(
            self.session,
            aggregate_type="consent_record",
            aggregate_id=consent_id,
            event_type="consent_signed",
            payload={"consent_id": str(consent_id), "consent_type": record["consent_type"], "signer_type": signer["signer_type"]},
        )
        signer_id = record["staff_id"] or record["patient_id"]
        await self.session.execute(text("UPDATE profiles SET consent_signed = TRUE WHERE id = :id"), {"id": str(signer_id)})

        # staff_onboarding activates immediately. patient_onboarding does not:
        # patients activate at registration_complete or receptionist approval.
        if record["consent_type"] == "staff_onboarding" and record["staff_id"]:
            await self.session.execute(text("UPDATE profiles SET is_active = TRUE WHERE id = :id"), {"id": str(record["staff_id"])})

        if record["consent_type"] == "patient_onboarding" and record["patient_id"]:
            # Local import — avoids a module-load-time circular import.
            from app.modules.patients.repository import PatientRepository
            from app.modules.patients.service import PatientService

            patient = await PatientRepository(self.session).get_by_profile_id(record["patient_id"])
            if patient:
                await PatientService(self.session).advance_registration_status(patient["patient_id"])

        # The DB row is the legal record; a PDF failure must not lose the
        # signature. GET /consent-records/{id}/pdf builds it later if missing.
        try:
            updated = await self.ensure_pdf(updated)
        except Exception:
            logger.warning("consent_pdf_failed", consent_id=str(consent_id), exc_info=True)
        return updated

    async def ensure_pdf(self, record: dict) -> dict:
        if record["pdf_s3_key"] or record["status"] == "pending" or not record.get("rendered_content"):
            return record
        from app.integrations.s3 import build_key, save_bytes
        from app.modules.consent.pdf import build_consent_pdf

        template = await self.templates.get(record["template_id"])
        clinic = await self._clinic(record["clinic_id"])
        subject = await self._subject(record)
        content = build_consent_pdf(
            record=record,
            template=template,  # type: ignore[arg-type]
            clinic_name=clinic["clinic_name"] if clinic else None,
            subject_name=f"{subject['first_name']} {subject['last_name']}".strip(),
        )
        key = build_key(
            "consent",
            region_id=str(clinic["region_id"]) if clinic else (str(record["region_id"]) if record["region_id"] else None),
            clinic_id=str(record["clinic_id"]) if record["clinic_id"] else None,
            patient_id=str(record["patient_id"]) if record["patient_id"] else None,
            staff_id=str(record["staff_id"]) if record["staff_id"] else None,
            template=record["consent_type"],
            version=template["version"],  # type: ignore[index]
        )
        _size, sha256, version_id = await asyncio.to_thread(save_bytes, key, content, bucket="compliance")
        stored = await self.repo.set_pdf(record["consent_id"], key=key, sha256=sha256, version_id=version_id)
        return stored or await self.get(record["consent_id"])

    async def pdf_link(self, record: dict) -> dict:
        if record["status"] == "pending":
            raise BusinessRuleError("This consent has not been signed yet", code="CONSENT_NOT_SIGNED")
        record = await self.ensure_pdf(record)
        if not record["pdf_s3_key"]:
            raise NotFoundError("No PDF exists for this consent (signed before PDFs were kept)", code="CONSENT_PDF_NOT_FOUND")
        from app.integrations.s3 import presign_download

        filename = f"{record['consent_type']}_{str(record['consent_id'])[:8]}.pdf"
        url = presign_download(record["pdf_s3_key"], filename=filename, version_id=record["pdf_version_id"], bucket="compliance")
        return {"url": url, "filename": filename}

    async def revoke(self, consent_id: UUID, *, ctx: RequestContext) -> dict:
        record = await self.get(consent_id)
        if ctx.user_id not in (str(record["patient_id"] or ""), str(record["staff_id"] or "")):
            raise PermissionError_("Only the person this consent belongs to can withdraw it", code="CONSENT_SUBJECT_ONLY")
        if record["status"] != "signed":
            raise BusinessRuleError("Only a signed consent can be revoked", code="CONSENT_NOT_SIGNED")
        updated = await self.repo.revoke(consent_id, revoked_by=UUID(ctx.user_id))
        await emit_event(
            self.session,
            aggregate_type="consent_record",
            aggregate_id=consent_id,
            event_type="consent_revoked",
            payload={"consent_id": str(consent_id)},
        )
        return updated  # type: ignore[return-value]
