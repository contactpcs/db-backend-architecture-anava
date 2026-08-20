from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning, update_returning


class TreatmentCycleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("treatment_cycles", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, cycle_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM treatment_cycles WHERE cycle_id = :id"), {"id": str(cycle_id)})

    async def list(self, *, patient_id: UUID | None = None, clinic_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if patient_id:
            clauses.append("patient_id = :pid")
            params["pid"] = str(patient_id)
        if clinic_id:
            clauses.append("clinic_id = :cid")
            params["cid"] = str(clinic_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (await self.session.execute(text(f"SELECT * FROM treatment_cycles {where} ORDER BY created_at DESC"), params)).mappings().all()
        )
        return [dict(r) for r in rows]

    async def get_active_for_patient(self, patient_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM treatment_cycles WHERE patient_id = :pid AND status = 'in_progress' ORDER BY cycle_number DESC LIMIT 1"),
            {"pid": str(patient_id)},
        )

    async def set_status(self, cycle_id: UUID, status: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE treatment_cycles SET status = :s WHERE cycle_id = :id RETURNING *"),
            {"s": status, "id": str(cycle_id)},
        )


class ProtocolRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("assessment_protocol_requests", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, request_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM assessment_protocol_requests WHERE request_id = :id"),
            {"id": str(request_id)},
        )

    async def list(self, *, patient_id: UUID | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], {}
        if patient_id:
            clauses.append("patient_id = :pid")
            params["pid"] = str(patient_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (await self.session.execute(text(f"SELECT * FROM assessment_protocol_requests {where} ORDER BY submitted_at DESC"), params))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def decide(self, request_id: UUID, *, status: str, doctor_notes: str | None) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE assessment_protocol_requests SET status = :status, doctor_notes = :notes, "
                "reviewed_at = NOW() WHERE request_id = :id RETURNING *"
            ),
            {"status": status, "notes": doctor_notes, "id": str(request_id)},
        )


class TreatmentPlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("treatment_plans", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, plan_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM treatment_plans WHERE plan_id = :id"), {"id": str(plan_id)})

    async def list(self, *, patient_id: UUID | None = None, cycle_id: UUID | None = None) -> list[dict]:
        clauses, params = [], {}
        if patient_id:
            clauses.append("patient_id = :pid")
            params["pid"] = str(patient_id)
        if cycle_id:
            clauses.append("cycle_id = :cid")
            params["cid"] = str(cycle_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (await self.session.execute(text(f"SELECT * FROM treatment_plans {where} ORDER BY created_at DESC"), params)).mappings().all()
        )
        return [dict(r) for r in rows]

    async def update(self, plan_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(plan_id)
        sql, params = update_returning("treatment_plans", "plan_id", str(plan_id), fields)
        return await fetch_optional(self.session, sql, params)

    async def supersede(self, plan_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE treatment_plans SET status = 'superseded' WHERE plan_id = :id RETURNING *"),
            {"id": str(plan_id)},
        )
