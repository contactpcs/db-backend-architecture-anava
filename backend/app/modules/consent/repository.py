from __future__ import annotations

import builtins
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class ConsentTemplateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active(self, consent_type: str, role: str | None = None, language: str = "en") -> dict | None:
        # role IS NOT DISTINCT FROM handles NULL = NULL — staff_onboarding has
        # one row per role, every other type has role IS NULL. Falls back to
        # English when the requested language has no active version.
        return await fetch_optional(
            self.session,
            text(
                "SELECT * FROM consent_templates WHERE consent_type = :t AND is_active "
                "AND role IS NOT DISTINCT FROM :role AND language IN (:lang, 'en') "
                "ORDER BY (language = :lang) DESC LIMIT 1"
            ),
            {"t": consent_type, "role": role, "lang": language},
        )

    async def list(self, *, include_drafts: bool) -> list[dict]:
        # Only the super admin (include_drafts) gets the full forms — up to 1 MB each.
        where, cols = (
            ("", "*")
            if include_drafts
            else (
                "WHERE published_at IS NOT NULL",
                "template_id, consent_type, role, language, version, title, content, clauses, content_hash, "
                "is_active, published_at, created_at, full_html IS NOT NULL AS has_full_html",
            )
        )
        sql = f"SELECT {cols} FROM consent_templates {where} ORDER BY consent_type, role NULLS FIRST, language, version DESC"
        rows = (await self.session.execute(text(sql))).mappings().all()
        return [dict(r) for r in rows]

    async def get(self, template_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM consent_templates WHERE template_id = :id"), {"id": str(template_id)})

    async def create_draft(
        self,
        *,
        consent_type: str,
        role: str | None,
        language: str,
        title: str,
        content: str,
        clauses: builtins.list,
        full_html: str | None = None,
    ) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO consent_templates (consent_type, role, language, version, title, content, clauses, full_html, is_active) "
                "SELECT :t, :role, :lang, COALESCE(MAX(version), 0) + 1, :title, :content, CAST(:clauses AS JSONB), :full_html, FALSE "
                "FROM consent_templates WHERE consent_type = :t AND role IS NOT DISTINCT FROM :role AND language = :lang "
                "RETURNING *"
            ),
            {
                "t": consent_type,
                "role": role,
                "lang": language,
                "title": title,
                "content": content,
                "clauses": json.dumps(clauses),
                "full_html": full_html,
            },
        )

    async def update_draft(self, template_id: UUID, fields: dict) -> dict | None:
        sets = ", ".join(f"{k} = CAST(:{k} AS JSONB)" if k == "clauses" else f"{k} = :{k}" for k in fields)
        params = {k: json.dumps(v) if k == "clauses" else v for k, v in fields.items()}
        return await fetch_optional(
            self.session,
            text(f"UPDATE consent_templates SET {sets} WHERE template_id = :id AND published_at IS NULL RETURNING *"),
            {**params, "id": str(template_id)},
        )

    async def delete_draft(self, template_id: UUID) -> bool:
        result = await self.session.execute(
            text("DELETE FROM consent_templates WHERE template_id = :id AND published_at IS NULL"), {"id": str(template_id)}
        )
        return result.rowcount == 1  # type: ignore[attr-defined]  # CursorResult has rowcount; async Result stubs don't expose it

    async def publish(self, template: dict) -> dict:
        # Deactivate first: one active row per (type, role, language) is a
        # unique index.
        await self.session.execute(
            text(
                "UPDATE consent_templates SET is_active = FALSE WHERE is_active AND consent_type = :t "
                "AND role IS NOT DISTINCT FROM :role AND language = :lang"
            ),
            {"t": template["consent_type"], "role": template["role"], "lang": template["language"]},
        )
        return await fetch_one(
            self.session,
            text("UPDATE consent_templates SET published_at = now(), is_active = TRUE WHERE template_id = :id RETURNING *"),
            {"id": str(template["template_id"])},
        )


class ConsentRecordRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, consent_type: str, template_id: UUID, patient_id, staff_id, clinic_id, region_id=None) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO consent_records (consent_type, template_id, patient_id, staff_id, clinic_id, region_id) "
                "VALUES (:consent_type, :template_id, :patient_id, :staff_id, :clinic_id, :region_id) RETURNING *"
            ),
            {
                "consent_type": consent_type,
                "template_id": str(template_id),
                "patient_id": str(patient_id) if patient_id else None,
                "staff_id": str(staff_id) if staff_id else None,
                "clinic_id": str(clinic_id) if clinic_id else None,
                "region_id": str(region_id) if region_id else None,
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
        rows = (
            (await self.session.execute(text(f"SELECT * FROM consent_records {where} ORDER BY created_at DESC"), params)).mappings().all()
        )
        return [dict(r) for r in rows]

    async def oldest_pending_for(self, profile_id: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT * FROM consent_records WHERE status = 'pending' AND (patient_id = :id OR staff_id = :id) "
                "ORDER BY created_at LIMIT 1"
            ),
            {"id": profile_id},
        )

    async def set_template(self, consent_id: UUID, template_id: UUID) -> dict:
        return await fetch_one(
            self.session,
            text("UPDATE consent_records SET template_id = :tid WHERE consent_id = :id AND status = 'pending' RETURNING *"),
            {"tid": str(template_id), "id": str(consent_id)},
        )

    async def sign(self, consent_id: UUID, evidence: dict) -> dict | None:
        """evidence: signed_by, signed_at, rendered_title/content/hash,
        rendered_full_hash, responses, fill_values, content_hash_at_signing,
        ip_address, user_agent, auth_method, signer_type/name/relation/contact,
        guardian_reason."""
        jsonb = ("responses", "fill_values")
        sets = ", ".join(
            f"{k} = CAST(:{k} AS JSONB)" if k in jsonb else f"{k} = CAST(:{k} AS INET)" if k == "ip_address" else f"{k} = :{k}"
            for k in evidence
        )
        params = {k: json.dumps(v) if k in jsonb else v for k, v in evidence.items()}
        return await fetch_optional(
            self.session,
            text(f"UPDATE consent_records SET status = 'signed', {sets} WHERE consent_id = :id AND status = 'pending' RETURNING *"),
            {**params, "id": str(consent_id)},
        )

    async def mark_full_viewed(self, consent_id: UUID) -> None:
        await self.session.execute(
            text(
                "UPDATE consent_records SET full_viewed_at = NOW() WHERE consent_id = :id AND status = 'pending' AND full_viewed_at IS NULL"
            ),
            {"id": str(consent_id)},
        )

    async def set_pdf(self, consent_id: UUID, *, key: str, sha256: str, version_id: str | None) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE consent_records SET pdf_s3_key = :key, pdf_sha256 = :sha, pdf_version_id = :vid, pdf_generated_at = NOW() "
                "WHERE consent_id = :id AND pdf_s3_key IS NULL RETURNING *"
            ),
            {"key": key, "sha": sha256, "vid": version_id, "id": str(consent_id)},
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

    async def require_patient_reconsent(self, template_id: UUID) -> int:
        """Every patient whose latest patient_onboarding signature is on an
        older template gets a new pending record and is gated to the consent
        screen (profiles.consent_signed = FALSE). Super admin only."""
        rows = (
            await self.session.execute(
                text(
                    "WITH targets AS ("
                    "  SELECT DISTINCT ON (cr.patient_id) cr.patient_id, cr.clinic_id, cr.region_id, cr.template_id "
                    "  FROM consent_records cr "
                    "  WHERE cr.consent_type = 'patient_onboarding' AND cr.status = 'signed' AND cr.patient_id IS NOT NULL "
                    "  ORDER BY cr.patient_id, cr.signed_at DESC"
                    "), fresh AS ("
                    "  SELECT t.* FROM targets t WHERE t.template_id <> :tid AND NOT EXISTS ("
                    "    SELECT 1 FROM consent_records p WHERE p.patient_id = t.patient_id "
                    "    AND p.consent_type = 'patient_onboarding' AND p.status = 'pending')"
                    "), inserted AS ("
                    "  INSERT INTO consent_records (consent_type, template_id, patient_id, clinic_id, region_id) "
                    "  SELECT 'patient_onboarding', :tid, patient_id, clinic_id, region_id FROM fresh RETURNING patient_id"
                    ") UPDATE profiles SET consent_signed = FALSE WHERE id IN (SELECT patient_id FROM inserted) RETURNING id"
                ),
                {"tid": str(template_id)},
            )
        ).all()
        return len(rows)
