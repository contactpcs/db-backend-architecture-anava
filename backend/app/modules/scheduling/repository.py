from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning


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
        rows = (await self.session.execute(text(f"SELECT * FROM doctor_schedule_overrides WHERE {clause}"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def for_date(self, doctor_id: UUID, on_date) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM doctor_schedule_overrides WHERE doctor_id = :id AND override_date = :d"),
            {"id": str(doctor_id), "d": on_date},
        )


class AppointmentRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointment_requests", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, request_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM appointment_requests WHERE request_id = :id"), {"id": str(request_id)})

    async def list(self, *, clinic_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if clinic_id:
            clauses.append("clinic_id = :cid")
            params["cid"] = str(clinic_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM appointment_requests {where} ORDER BY created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def set_decision(self, request_id: UUID, *, status: str, reviewed_by: UUID, review_notes, approved_appointment_id=None) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE appointment_requests SET status = :status, reviewed_by = :reviewed_by, review_notes = :notes, "
                "approved_appointment_id = :appt_id WHERE request_id = :id RETURNING *"
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
        return await fetch_optional(self.session, text("SELECT * FROM appointments WHERE appointment_id = :id"), {"id": str(appointment_id)})

    async def list(self, *, clinic_id: UUID | None = None, doctor_id: UUID | None = None, patient_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if clinic_id:
            clauses.append("clinic_id = :cid")
            params["cid"] = str(clinic_id)
        if doctor_id:
            clauses.append("doctor_id = :did")
            params["did"] = str(doctor_id)
        if patient_id:
            clauses.append("patient_id = :pid")
            params["pid"] = str(patient_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"SELECT * FROM appointments {where} ORDER BY appointment_date, start_time"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def list_for_doctor_on_date(self, doctor_id: UUID, on_date) -> list[dict]:
        rows = (
            await self.session.execute(
                text("SELECT * FROM appointments WHERE doctor_id = :id AND appointment_date = :d AND status NOT IN ('cancelled', 'rescheduled')"),
                {"id": str(doctor_id), "d": on_date},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def update_status(self, appointment_id: UUID, *, status: str, cancelled_by=None, cancellation_reason=None) -> dict | None:
        extra_cols = ""
        params = {"status": status, "id": str(appointment_id)}
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
            text(f"UPDATE appointments SET status = :status {extra_cols} WHERE appointment_id = :id RETURNING *"),
            params,
        )

    async def reschedule(self, appointment_id: UUID, *, new_appointment_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE appointments SET status = 'rescheduled', rescheduled_to = :new_id WHERE appointment_id = :id RETURNING *"),
            {"new_id": str(new_appointment_id), "id": str(appointment_id)},
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
