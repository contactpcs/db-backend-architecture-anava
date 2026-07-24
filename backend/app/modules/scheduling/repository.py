from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning

# appointments.patient_id / appointments.doctor_id / appointment_requests.
# patient_id / .doctor_id all store profiles(id) directly (doctors.profile_id
# IS a profiles.id) — so hydrating a name is always a plain join on
# profiles.id, never a second hop through patients/doctors.
_APPT_SELECT = (
    "SELECT a.*, pp.first_name || ' ' || pp.last_name AS patient_name, "
    "dp.first_name || ' ' || dp.last_name AS doctor_name, "
    # doctors.doctor_id (public ID) — /doctors/{doctor_id}/availability and
    # similar path params expect this, not a.doctor_id (profiles.id). Same
    # gap as _REQ_SELECT's doctor_public_id, fixed for the same reason.
    "dd.doctor_id AS doctor_public_id "
    "FROM appointments a "
    "JOIN profiles pp ON pp.id = a.patient_id "
    "JOIN profiles dp ON dp.id = a.doctor_id "
    "LEFT JOIN doctors dd ON dd.profile_id = a.doctor_id "
)

_REQ_SELECT = (
    "SELECT r.*, pp.first_name || ' ' || pp.last_name AS patient_name, "
    "dp.first_name || ' ' || dp.last_name AS doctor_name, "
    # doctors.doctor_id (public ID) — path params like /doctors/{doctor_id}/
    # availability expect this, not r.doctor_id (which is profiles.id, same
    # value space as doctors.profile_id). Without this, a caller resolving
    # slots straight from a request row 404s against the availability
    # endpoint (see doctor/schedule page's identical bug, fixed via /auth/me).
    "dd.doctor_id AS doctor_public_id, "
    "rp.first_name || ' ' || rp.last_name AS reviewer_name "
    "FROM appointment_requests r "
    "JOIN profiles pp ON pp.id = r.patient_id "
    "LEFT JOIN profiles dp ON dp.id = r.doctor_id "
    "LEFT JOIN doctors dd ON dd.profile_id = r.doctor_id "
    "LEFT JOIN profiles rp ON rp.id = r.reviewed_by "
)


class WeeklyScheduleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("doctor_weekly_schedules", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_doctor(self, doctor_id: UUID) -> list[dict]:
        rows = (
            await self.session.execute(
                text("SELECT * FROM doctor_weekly_schedules WHERE doctor_id = :id AND is_active = TRUE ORDER BY day_of_week"),
                {"id": str(doctor_id)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def replace_for_doctor(self, doctor_id: UUID, clinic_id: UUID, items: list[dict], *, created_by: UUID) -> list[dict]:
        """Delete-then-insert atomic replace (v1's upsert_weekly_schedule) —
        a doctor redrawing their whole week submits the full set at once,
        there's no natural per-day PATCH for that UX."""
        await self.session.execute(text("DELETE FROM doctor_weekly_schedules WHERE doctor_id = :id"), {"id": str(doctor_id)})
        created = []
        for item in items:
            payload = {**item, "doctor_id": str(doctor_id), "clinic_id": str(clinic_id), "created_by": str(created_by)}
            sql, params = insert_returning("doctor_weekly_schedules", payload)
            created.append(await fetch_one(self.session, sql, params))
        return created


class ScheduleOverrideRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("doctor_schedule_overrides", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_doctor(self, doctor_id: UUID, *, from_date=None) -> list[dict]:
        clause = "doctor_id = :id"
        params = {"id": str(doctor_id)}
        if from_date:
            clause += " AND override_date >= :from_date"
            params["from_date"] = from_date
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM doctor_schedule_overrides WHERE {clause} ORDER BY override_date"), params
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def for_date(self, doctor_id: UUID, on_date) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM doctor_schedule_overrides WHERE doctor_id = :id AND override_date = :d"),
            {"id": str(doctor_id), "d": on_date},
        )

    async def for_range(self, doctor_id: UUID, from_date, to_date) -> dict:
        """Keyed by date — one query for the whole range instead of one
        per day when computing multi-day availability."""
        rows = (
            await self.session.execute(
                text("SELECT * FROM doctor_schedule_overrides WHERE doctor_id = :id AND override_date BETWEEN :f AND :t"),
                {"id": str(doctor_id), "f": from_date, "t": to_date},
            )
        ).mappings().all()
        return {r["override_date"]: dict(r) for r in rows}

    async def get(self, override_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM doctor_schedule_overrides WHERE override_id = :id"),
            {"id": str(override_id)},
        )

    async def delete(self, override_id: UUID) -> bool:
        result = await self.session.execute(text("DELETE FROM doctor_schedule_overrides WHERE override_id = :id"), {"id": str(override_id)})
        return result.rowcount > 0  # type: ignore[attr-defined]  # CursorResult has rowcount; async Result stubs don't expose it


class AppointmentRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointment_requests", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, request_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(_REQ_SELECT + "WHERE r.request_id = :id"), {"id": str(request_id)})

    async def list(self, *, clinic_id: UUID | None = None, region_id: UUID | None = None, patient_id: UUID | None = None,
                   doctor_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if clinic_id:
            clauses.append("r.clinic_id = :cid")
            params["cid"] = str(clinic_id)
        elif region_id:
            clauses.append("r.clinic_id IN (SELECT clinic_id FROM clinics WHERE region_id = :rid)")
            params["rid"] = str(region_id)
        if patient_id:
            clauses.append("r.patient_id = :pid")
            params["pid"] = str(patient_id)
        if doctor_id:
            clauses.append("r.doctor_id = :did")
            params["did"] = str(doctor_id)
        if status:
            clauses.append("r.status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"{_REQ_SELECT}{where} ORDER BY r.created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def find_pending(self, *, patient_id: UUID, request_type: str, parent_appointment_id: UUID | None = None) -> dict | None:
        clause = "patient_id = :pid AND request_type = :rt AND status = 'pending'"
        params = {"pid": str(patient_id), "rt": request_type}
        if parent_appointment_id:
            clause += " AND parent_appointment_id = :parent"
            params["parent"] = str(parent_appointment_id)
        return await fetch_optional(self.session, text(f"SELECT * FROM appointment_requests WHERE {clause} LIMIT 1"), params)

    async def set_decision(
        self, request_id: UUID, *, status: str, reviewed_by: UUID, review_notes, approved_appointment_id=None
    ) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE appointment_requests SET status = :status, reviewed_by = :reviewed_by, review_notes = :notes, "
                "approved_appointment_id = :appt_id, updated_at = NOW() WHERE request_id = :id RETURNING *"
            ),
            {"status": status, "reviewed_by": str(reviewed_by), "notes": review_notes,
             "appt_id": str(approved_appointment_id) if approved_appointment_id else None, "id": str(request_id)},
        )


class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointments", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, appointment_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(_APPT_SELECT + "WHERE a.appointment_id = :id"), {"id": str(appointment_id)})

    async def list(self, *, clinic_id: UUID | None = None, region_id: UUID | None = None, doctor_id: UUID | None = None,
                   patient_id: UUID | None = None, status: str | None = None, date_from=None, date_to=None,
                   skip: int = 0, limit: int = 100) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {}
        if clinic_id:
            clauses.append("a.clinic_id = :cid")
            params["cid"] = str(clinic_id)
        elif region_id:
            clauses.append("a.clinic_id IN (SELECT clinic_id FROM clinics WHERE region_id = :rid)")
            params["rid"] = str(region_id)
        if doctor_id:
            clauses.append("a.doctor_id = :did")
            params["did"] = str(doctor_id)
        if patient_id:
            clauses.append("a.patient_id = :pid")
            params["pid"] = str(patient_id)
        if status:
            clauses.append("a.status = :status")
            params["status"] = status
        if date_from:
            clauses.append("a.appointment_date >= :date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("a.appointment_date <= :date_to")
            params["date_to"] = date_to
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params["skip"], params["limit"] = skip, limit
        rows = (
            await self.session.execute(
                text(f"{_APPT_SELECT}{where} ORDER BY a.appointment_date, a.start_time OFFSET :skip LIMIT :limit"), params
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def list_for_doctor_on_date(self, doctor_id: UUID, on_date) -> builtins.list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT * FROM appointments WHERE doctor_id = :id AND appointment_date = :d "
                    "AND status NOT IN ('cancelled', 'rescheduled')"
                ),
                {"id": str(doctor_id), "d": on_date},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def list_for_doctor_in_range(self, doctor_id: UUID, from_date, to_date) -> builtins.list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT * FROM appointments WHERE doctor_id = :id AND appointment_date BETWEEN :f AND :t "
                    "AND status NOT IN ('cancelled', 'rescheduled')"
                ),
                {"id": str(doctor_id), "f": from_date, "t": to_date},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def update_status(self, appointment_id: UUID, *, status: str, cancelled_by=None, cancellation_reason=None) -> dict | None:
        extra_cols = ""
        params: dict[str, Any] = {"status": status, "id": str(appointment_id)}
        if status == "cancelled":
            extra_cols = ", cancelled_by = :cancelled_by, cancellation_reason = :reason"
            params["cancelled_by"] = str(cancelled_by) if cancelled_by else None
            params["reason"] = cancellation_reason
        elif status == "checked_in":
            extra_cols = ", checked_in_at = NOW()"
        elif status == "in_progress":
            extra_cols = ", started_at = NOW()"
        elif status == "completed":
            extra_cols = ", completed_at = NOW()"
        return await fetch_optional(
            self.session,
            text(f"UPDATE appointments SET status = :status, updated_at = NOW() {extra_cols} WHERE appointment_id = :id RETURNING *"),
            params,
        )

    async def update_fields(self, appointment_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(appointment_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        return await fetch_optional(
            self.session,
            text(f"UPDATE appointments SET {set_clause}, updated_at = NOW() WHERE appointment_id = :id RETURNING *"),
            {**fields, "id": str(appointment_id)},
        )

    async def reschedule(self, appointment_id: UUID, *, new_appointment_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE appointments SET status = 'rescheduled', rescheduled_to = :new_id, "
                "updated_at = NOW() WHERE appointment_id = :id RETURNING *"
            ),
            {"new_id": str(new_appointment_id), "id": str(appointment_id)},
        )

    async def link_session(self, appointment_id: UUID, *, session_id: UUID) -> None:
        await self.session.execute(
            text("UPDATE appointments SET session_id = :sid WHERE appointment_id = :id"),
            {"sid": str(session_id), "id": str(appointment_id)},
        )


class AppointmentAuditLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointment_audit_logs", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_appointment(self, appointment_id: UUID) -> list[dict]:
        rows = (
            await self.session.execute(
                text("SELECT * FROM appointment_audit_logs WHERE appointment_id = :id ORDER BY changed_at"),
                {"id": str(appointment_id)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
