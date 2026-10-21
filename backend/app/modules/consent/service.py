from __future__ import annotations

import asyncio
import builtins
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionError_, ValidationError
from app.modules.consent import fill
from app.modules.consent.fill import hash_rendered, render
from app.modules.consent.repository import ConsentRecordRepository, ConsentTemplateRepository

logger = structlog.get_logger()
settings = get_settings()

_STAFF_ROLES = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
_TRANSFER_TYPES = ("patient_clinic_transfer", "patient_relocation_transfer")
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


# ─── Signer + values ─────────────────────────────────────────────────────


def age_on(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _contact(subject: dict) -> str | None:
    contact = subject.get("phone") or subject.get("email")
    return None if contact and contact.endswith("@no-email.local") else contact


def resolve_signer(subject: dict, today: date) -> dict:
    """Who signs: the patient, or their guardian when the patient is a minor
    (mandatory) or a guardian is on file (adults under assisted care). Staff
    always sign for themselves."""
    full_name = f"{subject['first_name']} {subject['last_name']}".strip()
    contact = _contact(subject)
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


def _date(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else ""


def _join(*parts) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _title(value: str | None) -> str:
    return (value or "").replace("_", " ").title()


def fmt_signed_at(at: datetime) -> str:
    return at.astimezone(_IST).strftime("%d %b %Y, %I:%M:%S %p IST")


def time_values(signed_at: datetime | None, today: date) -> dict[str, str]:
    """[CONSENT_DATE]/[SIGNED_AT]: real values once signed, a stand-in before."""
    if signed_at is None:
        return {"CONSENT_DATE": _date(today), "SIGNED_AT": "On acceptance"}
    return {"CONSENT_DATE": _date(signed_at.astimezone(_IST).date()), "SIGNED_AT": fmt_signed_at(signed_at)}


def build_values(
    *,
    subject: dict,
    clinic: dict | None,
    signer: dict,
    record: dict,
    version: int,
    today: date,
    region_name: str | None = None,
    transfer: dict | None = None,
) -> dict[str, str]:
    """Every catalogue placeholder's value for this signer (fill.py, H6).
    Empty string = unknown (shown as "—")."""
    clinic = clinic or {}
    name = f"{subject['first_name']} {subject['last_name']}".strip()
    email = subject.get("email") or ""
    if email.endswith("@no-email.local"):
        email = ""
    guardian = signer["signer_type"] == "guardian"
    values = {
        "SIGNER_NAME": signer["name"],
        "CLINIC_NAME": clinic.get("clinic_name") or "",
        "CLINIC_ADDRESS": clinic.get("full_address")
        or _join(clinic.get("address"), clinic.get("city"), clinic.get("state"), clinic.get("pincode")),
        "CLINIC_PHONE": clinic.get("phone") or "",
        "CONSENT_ID": str(record["consent_id"]),
        "CONSENT_VERSION": str(version),
        "PARTNER_CLINIC_NAME": clinic.get("clinic_name") or "",
    }
    if subject["role"] == "patient":
        age = age_on(subject.get("dob"), today)
        values |= {
            "PATIENT_NAME": name,
            "PATIENT_DOB": _date(subject.get("dob")),
            "PATIENT_AGE": "" if age is None else str(age),
            "PATIENT_GENDER": _title(subject.get("gender")),
            "PATIENT_MRN": subject.get("mrn") or "",
            "PATIENT_PHONE": subject.get("phone") or "",
            "PATIENT_EMAIL": email,
            "PATIENT_ADDRESS": _join(subject.get("address"), subject.get("city"), subject.get("state"), subject.get("pincode")),
            "GUARDIAN_NAME": signer["name"] if guardian else "",
            "GUARDIAN_RELATION": _title(signer.get("relation")) if guardian else "",
            "GUARDIAN_PHONE": (signer.get("contact") or "") if guardian else "",
            "GUARDIAN_REASON": fill.GUARDIAN_REASON_LABELS.get(signer.get("guardian_reason") or "", "") if guardian else "",
            "FROM_CLINIC": (transfer or {}).get("from_clinic") or "",
            "TO_CLINIC": (transfer or {}).get("to_clinic") or "",
        }
    else:
        role = _title(subject["role"])
        values |= {
            "STAFF_NAME": name,
            "STAFF_ROLE": role,
            "ROLE": role,
            "STAFF_EMAIL": email,
            "STAFF_PHONE": subject.get("phone") or "",
            "STAFF_DOB": _date(subject.get("dob")),
            "STAFF_GENDER": _title(subject.get("gender")),
            "DOCTOR_LICENSE_NO": subject.get("license_number") or "",
            "DOCTOR_SPECIALIZATION": subject.get("specialization") or "",
            "REGION_NAME": region_name or "",
        }
    return values


def build_responses(clauses: list[dict], accepted: list[str]) -> list[dict]:
    codes = {c["code"] for c in clauses}
    unknown = sorted(set(accepted) - codes)
    if unknown:
        raise ValidationError(f"Unknown consent clause(s): {', '.join(unknown)}", code="CONSENT_UNKNOWN_CLAUSE")
    missing = [c["code"] for c in clauses if c["required"] and c["code"] not in accepted]
    if missing:
        raise ValidationError("Every required statement must be ticked", code="CONSENT_REQUIRED_CLAUSE", details=[{"missing": missing}])
    return [{**c, "accepted": c["code"] in accepted} for c in clauses]


def evidence_of(record: dict) -> dict:
    """What [SIGNATURE_BLOCK] and the PDF evidence page show for a signed record."""
    return {
        "signer_type": record["signer_type"],
        "name": record["signer_name"],
        "relation": record.get("signer_relation"),
        "on_behalf_of": (record.get("fill_values") or {}).get("PATIENT_NAME"),
        "signed_at": fmt_signed_at(record["signed_at"]) if record.get("signed_at") else None,
        "ip_address": record.get("ip_address"),
        "consent_id": record["consent_id"],
        "rendered_hash": record.get("rendered_hash"),
    }


def validate_template(consent_type: str, *, title: str, content: str, clauses: list[dict], full_html: str | None) -> None:
    """H4 + H6: placeholders this type can fill, and a safe full form."""
    labels = [c["label"] for c in clauses]
    # H19: only the summary, title and checkboxes must use known fields. The
    # full form may contain names Anava cannot fill (shown blank); only its
    # required fields and safety are enforced (publish / check_full_html).
    bad = fill.check_placeholders(consent_type, title, content, *labels)
    if bad:
        raise ValidationError(
            f"Unknown placeholder(s) for {consent_type}: {', '.join(f'[{b}]' for b in bad)}",
            code="CONSENT_TEMPLATE_PLACEHOLDER",
            details=[{"placeholders": bad}],
        )
    if any("[SIGNATURE_BLOCK]" in t for t in (title, content, *labels)):
        raise ValidationError("[SIGNATURE_BLOCK] can only be used in the full form", code="CONSENT_TEMPLATE_PLACEHOLDER")
    personal = fill.personal_placeholders(title, content, *labels)
    if personal:
        # H16: the summary page shows no personal details; they belong in the full form.
        raise ValidationError(
            f"Personal details can only go in the full form, not the title, summary or checkboxes: {', '.join(f'[{p}]' for p in personal)}",
            code="CONSENT_TEMPLATE_PERSONAL_IN_SUMMARY",
            details=[{"placeholders": personal}],
        )
    if full_html:
        problems = fill.check_full_html(full_html)
        if problems:
            raise ValidationError(
                f"The full form cannot be used: {problems[0]}" + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""),
                code="CONSENT_TEMPLATE_UNSAFE_HTML",
                details=[{"problems": problems}],
            )


# ─── Templates (super admin) ─────────────────────────────────────────────

_SAMPLE_SUBJECT = {
    "first_name": "Asha",
    "last_name": "Rao",
    "dob": date(2012, 4, 9),
    "gender": "female",
    "phone": "+919876543210",
    "email": "asha.rao@example.com",
    "address": "12 MG Road",
    "city": "Bengaluru",
    "state": "Karnataka",
    "pincode": "560001",
    "mrn": "ANV-000123",
    "license_number": "KMC-45678",
    "specialization": "Neurology",
}
_SAMPLE_CLINIC = {
    "clinic_name": "Anava Indiranagar",
    "full_address": "100 Feet Road, Indiranagar, Bengaluru 560038",
    "phone": "+918040000000",
}


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
        data["full_html"] = data.get("full_html") or None
        validate_template(
            data["consent_type"], title=data["title"], content=data["content"], clauses=data["clauses"], full_html=data["full_html"]
        )
        return await self.repo.create_draft(**data)

    async def update_draft(self, template_id: UUID, fields: dict) -> dict:
        template = await self._get(template_id)
        if template["published_at"] is not None:
            raise ConflictError("A published template cannot be changed — create a new version", code="CONSENT_TEMPLATE_PUBLISHED")
        if not fields:
            return template
        if "full_html" in fields:
            fields["full_html"] = fields["full_html"] or None  # "" removes the full form
        merged = {**template, **fields}
        validate_template(
            template["consent_type"],
            title=merged["title"],
            content=merged["content"],
            clauses=merged["clauses"],
            full_html=merged["full_html"],
        )
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
        validate_template(
            template["consent_type"],
            title=template["title"],
            content=template["content"],
            clauses=template["clauses"],
            full_html=template["full_html"],
        )
        if template["full_html"]:
            # H18: drafts may be incomplete; a published full form must name
            # who it is for, when it was accepted, and the acceptance itself.
            missing = fill.analyze_full_html(template["consent_type"], template["role"], template["full_html"])["missing_required"]
            if missing:
                raise ValidationError(
                    f"The full form is missing required field(s): {', '.join(f'[{m}]' for m in missing)}",
                    code="CONSENT_TEMPLATE_MISSING_FIELDS",
                    details=[{"missing": missing}],
                )
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

    @staticmethod
    def placeholders(consent_type: str, role: str | None = None) -> builtins.list[dict]:  # `list` is a method here
        required = fill.required_placeholders(consent_type, role)
        recommended = fill.recommended_placeholders(consent_type, role)
        return [
            {
                "name": p,
                "description": fill.DESCRIPTIONS[p],
                "full_form_only": p in fill.PERSONAL or p == "SIGNATURE_BLOCK",
                "level": "required" if p in required else "recommended" if p in recommended else "optional",
            }
            for p in fill.allowed_placeholders(consent_type)
        ]

    async def preview(self, template_id: UUID, *, signer_type: str, fmt: str) -> tuple[bytes, str]:
        """The template filled with sample details exactly as a signed copy
        looks: (body, media type). Nothing is stored."""
        template = await self._get(template_id)
        is_staff = template["consent_type"].startswith("staff_")
        subject = {**_SAMPLE_SUBJECT, "role": (template["role"] or "doctor") if is_staff else "patient"}
        if is_staff or signer_type != "guardian":
            subject["dob"] = date(1990, 4, 9)
        else:
            subject |= {"guardian_name": "Ravi Rao", "guardian_relationship": "father", "guardian_contact": "+919876543210"}
        now = datetime.now(UTC)
        today = now.astimezone(_IST).date()
        signer = resolve_signer(subject, today)
        record = {"consent_id": UUID(int=0), "consent_type": template["consent_type"]}
        values = build_values(
            subject=subject,
            clinic=_SAMPLE_CLINIC,
            signer=signer,
            record=record,
            version=template["version"],
            today=today,
            region_name="Karnataka",
            transfer={"from_clinic": "Anava Indiranagar", "to_clinic": "Anava Jubilee Hills"},
        )
        canonical = render(template, values, signer["signer_type"], times=None)
        sample = {
            **record,
            "status": "signed",
            "signer_type": signer["signer_type"],
            "signer_name": signer["name"],
            "signer_relation": signer.get("relation"),
            "signer_contact": signer.get("contact"),
            "guardian_reason": signer.get("guardian_reason"),
            "signed_at": now,
            "ip_address": "203.0.113.10",
            "user_agent": "Preview",
            "auth_method": "preview",
            "rendered_hash": hash_rendered(canonical),
            "content_hash_at_signing": template["content_hash"],
            "fill_values": fill.used_values(template, values),
        }
        final = render(
            template, values, signer["signer_type"], times=time_values(now, today), signature=fill.signature_block(evidence_of(sample))
        )
        sample |= {
            "rendered_title": final["title"],
            "rendered_content": final["content"],
            "responses": [{**c, "accepted": True} for c in final["clauses"]],
        }
        if fmt == "html":
            if not final.get("full_html"):
                raise NotFoundError("This template has no full form", code="CONSENT_NO_FULL_FORM")
            return final["full_html"].encode("utf-8"), "text/html; charset=utf-8"
        from app.modules.consent.pdf import build_consent_pdf, build_full_consent_pdf

        name = f"{subject['first_name']} {subject['last_name']}"
        if final.get("full_html"):
            pdf = await asyncio.to_thread(
                build_full_consent_pdf,
                full_html=final["full_html"],
                record=sample,
                template=template,
                clinic_name=_SAMPLE_CLINIC["clinic_name"],
                subject_name=name,
            )
        else:
            pdf = build_consent_pdf(record=sample, template=template, clinic_name=_SAMPLE_CLINIC["clinic_name"], subject_name=name)
        return pdf, "application/pdf"


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
                        "SELECT p.id, p.role, p.first_name, p.last_name, p.dob, p.gender, p.phone, p.email, "
                        "p.address, p.city, p.state, p.pincode, pt.patient_id AS patient_pk, pt.mrn, "
                        "pt.guardian_name, pt.guardian_relationship, pt.guardian_contact, pt.guardian_reason, "
                        "d.license_number, d.specialization "
                        "FROM profiles p LEFT JOIN patients pt ON pt.profile_id = p.id "
                        "LEFT JOIN doctors d ON d.profile_id = p.id WHERE p.id = :id"
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
            (
                await self.session.execute(
                    text(
                        "SELECT clinic_name, region_id, full_address, address, city, state, pincode, phone "
                        "FROM clinics WHERE clinic_id = :id"
                    ),
                    {"id": str(clinic_id)},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def _region_name(self, region_id) -> str | None:
        if not region_id:
            return None
        return (await self.session.execute(text("SELECT region_name FROM regions WHERE region_id = :id"), {"id": str(region_id)})).scalar()

    async def _transfer(self, record: dict) -> dict | None:
        if record["consent_type"] not in _TRANSFER_TYPES or not record["patient_id"]:
            return None
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT fc.clinic_name AS from_clinic, tc.clinic_name AS to_clinic FROM patient_clinic_transfers t "
                        "LEFT JOIN clinics fc ON fc.clinic_id = t.from_clinic_id LEFT JOIN clinics tc ON tc.clinic_id = t.to_clinic_id "
                        "WHERE t.patient_id = :pid AND (t.consent_id = :cid OR t.status = 'pending') "
                        "ORDER BY (t.consent_id = :cid) DESC NULLS LAST, t.created_at DESC LIMIT 1"
                    ),
                    {"pid": str(record["patient_id"]), "cid": str(record["consent_id"])},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def _pending_context(self, record: dict) -> dict:
        """Template, signer and values for a pending record, plus its
        canonical render (hashed) and display render (shown)."""
        template = await self.templates.get(record["template_id"])
        assert template is not None
        subject = await self._subject(record)
        today = datetime.now(_IST).date()
        signer = resolve_signer(subject, today)
        clinic = await self._clinic(record["clinic_id"])
        values = build_values(
            subject=subject,
            clinic=clinic,
            signer=signer,
            record=record,
            version=template["version"],
            today=today,
            region_name=(
                await self._region_name(record["region_id"] or (clinic or {}).get("region_id")) if subject["role"] != "patient" else None
            ),
            transfer=await self._transfer(record),
        )
        canonical = render(template, values, signer["signer_type"], times=None)
        display = render(template, values, signer["signer_type"], times=time_values(None, today), signature=fill.signature_block(None))
        return {
            "template": template,
            "subject": subject,
            "signer": signer,
            "values": values,
            "today": today,
            "canonical": canonical,
            "display": display,
        }

    def _final_full_html(self, record: dict, template: dict) -> str | None:
        """The signed full form, rebuilt from the record's snapshot (H9)."""
        if not template.get("full_html") or record.get("fill_values") is None or not record.get("signed_at"):
            return None
        out = render(
            template,
            record["fill_values"],
            record["signer_type"] or "self",
            times=time_values(record["signed_at"], record["signed_at"].astimezone(_IST).date()),
            signature=fill.signature_block(evidence_of(record)),
        )
        full = out["full_html"]
        if record.get("rendered_full_hash") and fill.sha256_hex(full) != record["rendered_full_hash"]:
            logger.error("consent_full_form_hash_mismatch", consent_id=str(record["consent_id"]))
        return full

    async def document(self, record: dict) -> dict:
        template = await self.templates.get(record["template_id"])
        assert template is not None
        if record["status"] == "pending":
            ctx = await self._pending_context(record)
            display = ctx["display"]
            return {
                **record,
                "version": template["version"],
                "language": template["language"],
                "title": display["title"],
                "content": display["content"],
                "clauses": display["clauses"],
                "rendered_hash": hash_rendered(ctx["canonical"]),
                "signer": ctx["signer"],
                "has_full_form": bool(template.get("full_html")),
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
            "has_full_form": bool(template.get("full_html") and record.get("fill_values") is not None),
            "pdf_available": bool(record.get("pdf_s3_key")),
        }

    async def full_form(self, record: dict, ctx: RequestContext) -> dict:
        """The full form (H1) filled for this record: as it would be signed now
        (pending) or exactly as signed. The subject's first look at a pending
        one is logged (H2)."""
        template = await self.templates.get(record["template_id"])
        assert template is not None
        if not template.get("full_html"):
            raise NotFoundError("This consent has no full form", code="CONSENT_NO_FULL_FORM")
        if record["status"] == "pending":
            full = (await self._pending_context(record))["display"]["full_html"]
            if ctx.user_id in (str(record["patient_id"] or ""), str(record["staff_id"] or "")) and record.get("full_viewed_at") is None:
                await self.repo.mark_full_viewed(record["consent_id"])
            return {"html": full}
        full = self._final_full_html(record, template)
        if full is None:
            raise NotFoundError("This consent was signed before full forms were kept", code="CONSENT_NO_FULL_FORM")
        return {"html": full}

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

        pc = await self._pending_context(record)
        template, signer, values = pc["template"], pc["signer"], pc["values"]
        # A newer version went live while the signer was reading: they must
        # accept the current one (GET /consent-records/me/pending moves the
        # record onto it), not the one they happened to load.
        role = ctx.role if record["consent_type"] == "staff_onboarding" else None
        active = await self.templates.get_active(record["consent_type"], role, template["language"])
        if not template["clauses"] or (active and active["template_id"] != template["template_id"]):
            raise ConflictError("This consent form was updated — reload it and review the new version", code="CONSENT_TEXT_CHANGED")
        if rendered_hash != hash_rendered(pc["canonical"]):
            raise ConflictError("The consent text changed since it was shown — reload and review it again", code="CONSENT_TEXT_CHANGED")

        signed_at = datetime.now(UTC)
        evidence = {
            "signed_by": ctx.user_id,
            "signed_at": signed_at,
            "rendered_hash": rendered_hash,
            "content_hash_at_signing": template["content_hash"],
            "ip_address": ctx.ip_address,
            "user_agent": (user_agent or "")[:500] or None,
            "auth_method": f"{settings.auth_mode}_password_session",
            "signer_type": signer["signer_type"],
            "signer_name": signer["name"],
            "signer_relation": signer.get("relation"),
            "signer_contact": signer.get("contact"),
            "guardian_reason": signer.get("guardian_reason"),
            "fill_values": fill.used_values(template, values),
        }
        signature = fill.signature_block(evidence_of({**record, **evidence}))
        final = render(template, values, signer["signer_type"], times=time_values(signed_at, pc["today"]), signature=signature)
        evidence |= {
            "rendered_title": final["title"],
            "rendered_content": final["content"],
            "responses": build_responses(final["clauses"], accepted),
            "rendered_full_hash": fill.sha256_hex(final["full_html"]) if final.get("full_html") else None,
        }

        updated = await self.repo.sign(consent_id, evidence)
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
        # signature. The savepoint keeps a DB error inside ensure_pdf from
        # aborting the signing transaction. GET .../pdf builds it later.
        # ponytail: built in-request (to_thread); move to the consent_signed
        # event if PDF CPU time shows up in sign latency.
        try:
            async with self.session.begin_nested():
                updated = await self.ensure_pdf(updated)
        except Exception:
            logger.warning("consent_pdf_failed", consent_id=str(consent_id), exc_info=True)
        return updated

    async def ensure_pdf(self, record: dict) -> dict:
        if record["pdf_s3_key"] or record["status"] == "pending" or not record.get("rendered_content"):
            return record
        from app.integrations.s3 import build_key, save_bytes
        from app.modules.consent.pdf import build_consent_pdf, build_full_consent_pdf

        template = await self.templates.get(record["template_id"])
        assert template is not None
        clinic = await self._clinic(record["clinic_id"])
        subject = await self._subject(record)
        clinic_name = clinic["clinic_name"] if clinic else None
        subject_name = f"{subject['first_name']} {subject['last_name']}".strip()
        full = self._final_full_html(record, template)
        if full:
            content = await asyncio.to_thread(
                build_full_consent_pdf, full_html=full, record=record, template=template, clinic_name=clinic_name, subject_name=subject_name
            )
        else:
            content = build_consent_pdf(record=record, template=template, clinic_name=clinic_name, subject_name=subject_name)
        key = build_key(
            "consent",
            region_id=str(clinic["region_id"]) if clinic else (str(record["region_id"]) if record["region_id"] else None),
            clinic_id=str(record["clinic_id"]) if record["clinic_id"] else None,
            # H11: the folder is the patients.patient_id, not the profile id.
            patient_id=str(subject["patient_pk"]) if record["patient_id"] and subject.get("patient_pk") else None,
            staff_id=str(record["staff_id"]) if record["staff_id"] else None,
            template=record["consent_type"],
            version=template["version"],
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
