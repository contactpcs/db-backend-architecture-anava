"""Reads behind the Final Report sections, plus the one INSERT into
core.final_reports (SQL/v1/117). Every patient_id here is profiles.id
(NOTES.md convention). There is no UPDATE or DELETE on purpose: a signed
report is immutable, and the table grants the app neither."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning

# What a consultation is (anamnesis/service.py CONSULTATION_TYPES), and the
# statuses it has once the doctor clicked "Start Consultation".
_CONSULTATION_TYPES = ["initial", "follow_up", "protocol_followup"]
_STARTED_STATUSES = ["in_progress", "completed"]

_META_COLUMNS = (
    "r.report_id, r.patient_id, r.clinic_id, r.doctor_id, r.scope, r.appointment_id, r.diagnosis, r.signature, r.content_hash, r.signed_at"
)
_REPORT_FROM = ", dp.first_name || ' ' || dp.last_name AS doctor_name FROM final_reports r LEFT JOIN profiles dp ON dp.id = r.doctor_id "


class FinalReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _all(self, sql: str, params: dict[str, Any]) -> list[dict]:
        return [dict(r) for r in (await self.session.execute(text(sql), params)).mappings().all()]

    # -- header ----------------------------------------------------------------

    async def patient_header(self, profile_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT p.first_name || ' ' || p.last_name AS name, p.dob, p.gender, pt.mrn "
                "FROM profiles p JOIN patients pt ON pt.profile_id = p.id WHERE p.id = :pid"
            ),
            {"pid": str(profile_id)},
        )

    async def doctor_header(self, doctor_profile_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT p.first_name || ' ' || p.last_name AS name, d.license_number, d.specialization "
                "FROM profiles p LEFT JOIN doctors d ON d.profile_id = p.id WHERE p.id = :id"
            ),
            {"id": str(doctor_profile_id)},
        )

    async def clinic_header(self, clinic_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT clinic_name, COALESCE(full_address, address) AS address, city, phone FROM clinics WHERE clinic_id = :id"),
            {"id": str(clinic_id)},
        )

    async def doctor_treats_patient(self, doctor_profile_id: UUID, profile_id: UUID) -> bool:
        """Actively assigned, or the doctor on one of the patient's
        consultations (a covering doctor who saw the patient may report on
        that visit)."""
        row = await fetch_one(
            self.session,
            text(
                "SELECT EXISTS (SELECT 1 FROM doctor_patient_assignments "
                "  WHERE doctor_id = :did AND patient_id = :pid AND status = 'active') "
                "OR EXISTS (SELECT 1 FROM appointments "
                "  WHERE doctor_id = :did AND patient_id = :pid AND appointment_type = ANY(:types)) AS ok"
            ),
            {"did": str(doctor_profile_id), "pid": str(profile_id), "types": _CONSULTATION_TYPES},
        )
        return bool(row["ok"])

    # -- sections --------------------------------------------------------------

    async def started_consultations(self, profile_id: UUID) -> list[dict]:
        """Oldest first — assembler.consultation_window reads [-1] and [-2]."""
        return await self._all(
            "SELECT appointment_id, appointment_type, appointment_date, status, started_at, completed_at "
            "FROM appointments WHERE patient_id = :pid AND appointment_type = ANY(:types) "
            "AND status = ANY(:statuses) AND started_at IS NOT NULL ORDER BY started_at",
            {"pid": str(profile_id), "types": _CONSULTATION_TYPES, "statuses": _STARTED_STATUSES},
        )

    async def anamnesis_assessments(self, profile_id: UUID, *, appointment_id: UUID | None = None) -> list[dict]:
        """Consultation anamnesis only, newest first — the registration
        anamnesis (self-filled at intake) is not part of the report and is
        never read. With appointment_id: that consultation's one only."""
        clause = "AND a.appointment_id = :aid" if appointment_id else ""
        return await self._all(
            "SELECT a.* FROM anamnesis_assessments a "
            f"WHERE a.patient_id = :pid AND a.assessment_stage = 'main' AND a.status <> 'superseded' {clause} "
            "ORDER BY a.created_at DESC",
            {"pid": str(profile_id), **({"aid": str(appointment_id)} if appointment_id else {})},
        )

    async def anamnesis_questions_with_answers(self, anamnesis_id: str, question_type: str) -> list[dict]:
        """Every active question of the catalogue (registration | main), with
        this assessment's answer when there is one."""
        return await self._all(
            "SELECT q.question_id, q.section_number, q.section_title, q.question_text, q.display_order, "
            "r.response_value, r.response_values "
            "FROM reference.anamnesis_questions q "
            "LEFT JOIN anamnesis_responses r ON r.question_id = q.question_id AND r.anamnesis_id = :id "
            "WHERE q.type = :type AND q.status ORDER BY q.section_number, q.display_order",
            {"id": anamnesis_id, "type": question_type},
        )

    async def anamnesis_option_labels(self) -> dict[tuple, str]:
        rows = await self._all("SELECT question_id, option_value, option_label FROM reference.anamnesis_options", {})
        return {(r["question_id"], r["option_value"]): r["option_label"] for r in rows}

    async def prs_instances(self, profile_id: UUID) -> list[dict]:
        """Every completed, non-voided clinical PRS of the patient, newest
        first, with where it was taken: the appointment it was started at,
        and — for a device session — that protocol's device and instance. The general
        registration PRS (taken at intake, before any condition is chosen) is
        not part of the report and is never read."""
        return await self._all(
            "SELECT i.instance_id, i.disease_id, d.disease_name, i.completed_at, i.assessment_stage, i.appointment_id, "
            "a.appointment_type, a.session_number, a.protocol_id, nd.device_name, "
            "pp.instance_id AS protocol_instance_id, pi.instance_number "
            "FROM prs_assessment_instances i "
            "LEFT JOIN reference.prs_diseases d ON d.disease_id = i.disease_id "
            "LEFT JOIN appointments a ON a.appointment_id = i.appointment_id "
            "LEFT JOIN protocol_plan pp ON pp.protocol_id = a.protocol_id "
            "LEFT JOIN protocol_instances pi ON pi.instance_id = pp.instance_id "
            "LEFT JOIN reference.neuromod_devices nd ON nd.device_id = pp.device_id "
            "WHERE i.patient_id = :pid AND i.status = 'completed' AND NOT COALESCE(i.is_voided, FALSE) "
            "AND i.assessment_stage <> 'general_registration' "
            "ORDER BY i.completed_at DESC NULLS LAST",
            {"pid": str(profile_id)},
        )

    async def conditions_by_protocol(self, protocol_ids: list[str]) -> dict[str, list[str]]:
        if not protocol_ids:
            return {}
        rows = await self._all(
            "SELECT pc.protocol_id, COALESCE(c.condition_name, pc.other_text) AS name FROM protocol_conditions pc "
            "LEFT JOIN reference.neuromod_conditions c ON c.condition_id = pc.condition_id "
            "WHERE pc.protocol_id = ANY(CAST(:ids AS UUID[])) ORDER BY c.display_order NULLS LAST, pc.created_at",
            {"ids": protocol_ids},
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(str(r["protocol_id"]), []).append(r["name"])
        return out

    async def device_sessions(self, profile_id: UUID) -> list[dict]:
        """Device sessions that were actually run (completed or stopped
        early), oldest first, with prescribed-vs-actual and event counts."""
        return await self._all(
            "SELECT a.appointment_id, a.session_number, a.appointment_date, ds.session_status, ds.started_at, "
            "ds.stopped_at, ds.completed_at, ds.actual_intensity_ma, ds.intensity_deviates, ds.actual_duration_min, "
            "ds.duration_deviates, ds.ramp_up_deviates, ds.ramp_down_deviates, ds.pause_stop_reason, "
            "pp.prescribed_current_ma, pp.prescribed_duration_min, nd.device_name, "
            "(SELECT count(*) FROM device_session_symptoms s "
            "  WHERE s.device_session_record_id = ds.device_session_record_id) AS symptom_count, "
            "(SELECT count(*) FROM device_session_adverse_events e "
            "  WHERE e.device_session_record_id = ds.device_session_record_id) AS adverse_event_count "
            "FROM device_sessions ds JOIN appointments a ON a.appointment_id = ds.appointment_id "
            "LEFT JOIN protocol_plan pp ON pp.protocol_id = ds.protocol_id "
            "LEFT JOIN reference.neuromod_devices nd ON nd.device_id = pp.device_id "
            "WHERE a.patient_id = :pid AND ds.session_status IN ('completed', 'stopped_early') "
            "ORDER BY a.appointment_date, a.session_number",
            {"pid": str(profile_id)},
        )

    # -- core.final_reports ----------------------------------------------------

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("final_reports", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, report_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(f"SELECT {_META_COLUMNS}, r.snapshot{_REPORT_FROM}WHERE r.report_id = :id"),
            {"id": str(report_id)},
        )

    async def list_for_patient(
        self,
        profile_id: UUID,
        *,
        scope: str | None = None,
        date_from=None,
        date_to=None,
        newest_first: bool = True,
    ) -> list[dict]:
        """Metadata only — the snapshot is fetched per report by get()."""
        clauses, params = ["r.patient_id = :pid"], {"pid": str(profile_id)}
        if scope:
            clauses.append("r.scope = :scope")
            params["scope"] = scope
        if date_from:
            clauses.append("r.signed_at::date >= :date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("r.signed_at::date <= :date_to")
            params["date_to"] = date_to
        order = "DESC" if newest_first else "ASC"
        return await self._all(f"SELECT {_META_COLUMNS}{_REPORT_FROM}WHERE {' AND '.join(clauses)} ORDER BY r.signed_at {order}", params)
