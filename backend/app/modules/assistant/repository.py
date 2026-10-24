"""Reads and writes for the Ask ANAVA assistant and session feedback
(SQL/v1/125). Every patient_id here is profiles.id (NOTES.md convention).
Request traffic runs as the patient, so RLS already limits every read to the
patient's own rows; the WHERE patient_id filters are for the query plan."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning

# Visits still ahead of the patient (scheduling/service.py status vocabulary).
ACTIVE_STATUSES = ["planned", "selected", "paid", "scheduled", "confirmed", "checked_in", "in_progress"]
_PAID_STATUSES = ["paid", "partially_refunded"]
_VISIT_COLUMNS = (
    "a.appointment_id, a.appointment_type, a.appointment_date, a.start_time, a.end_time, a.status, a.session_number, "
    "a.clinic_id, a.doctor_id, a.protocol_id, a.completed_at, tp.session_count, dev.device_name "
)
_VISIT_FROM = (
    "FROM appointments a "
    "LEFT JOIN protocol_plan tp ON tp.protocol_id = a.protocol_id "
    "LEFT JOIN reference.neuromod_devices dev ON dev.device_id = tp.device_id "
)


class AssistantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _all(self, sql: str, params: dict[str, Any]) -> list[dict]:
        return [dict(r) for r in (await self.session.execute(text(sql), params)).mappings().all()]

    # -- the patient -------------------------------------------------------------

    async def profile(self, pid: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT first_name, last_name FROM profiles WHERE id = :pid"), {"pid": str(pid)})

    async def clinic(self, pid: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT c.clinic_name, COALESCE(c.full_address, c.address) AS address, c.city, c.phone, c.email "
                "FROM patients pt JOIN clinics c ON c.clinic_id = pt.primary_clinic_id WHERE pt.profile_id = :pid"
            ),
            {"pid": str(pid)},
        )

    async def upcoming(self, pid: UUID, today: date) -> list[dict]:
        return await self._all(
            "SELECT " + _VISIT_COLUMNS + ", "
            "(SELECT p.amount FROM payments p WHERE p.appointment_id = a.appointment_id AND p.status IN ('created', 'pending') "
            " ORDER BY p.created_at DESC LIMIT 1) AS amount_due "
            + _VISIT_FROM
            + "WHERE a.patient_id = :pid AND a.status = ANY(:active) AND a.appointment_date >= :today "
            "ORDER BY a.appointment_date, a.start_time NULLS LAST LIMIT 10",
            {"pid": str(pid), "active": ACTIVE_STATUSES, "today": today},
        )

    async def last_completed(self, pid: UUID) -> dict | None:
        rows = await self._all(
            "SELECT " + _VISIT_COLUMNS + _VISIT_FROM + "WHERE a.patient_id = :pid AND a.status = 'completed' "
            "ORDER BY a.appointment_date DESC, a.start_time DESC NULLS LAST LIMIT 1",
            {"pid": str(pid)},
        )
        return rows[0] if rows else None

    async def session_counts(self, pid: UUID) -> dict:
        return await fetch_one(
            self.session,
            text(
                "SELECT count(*) FILTER (WHERE status = 'completed') AS done, count(*) FILTER (WHERE status = ANY(:active)) AS left "
                "FROM appointments WHERE patient_id = :pid AND appointment_type = 'device_session'"
            ),
            {"pid": str(pid), "active": ACTIVE_STATUSES},
        )

    async def latest_refund(self, pid: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT p.refund_status, COALESCE(p.cancellation_refund_amount, p.amount) AS refund_amount "
                "FROM payments p JOIN appointments a ON a.appointment_id = p.appointment_id "
                "WHERE a.patient_id = :pid AND p.refund_status IS NOT NULL ORDER BY p.created_at DESC LIMIT 1"
            ),
            {"pid": str(pid)},
        )

    async def total_paid(self, pid: UUID) -> dict:
        return await fetch_one(
            self.session,
            text(
                "SELECT COALESCE(sum(p.amount), 0) AS total, count(DISTINCT p.appointment_id) AS count "
                "FROM payments p JOIN appointments a ON a.appointment_id = p.appointment_id "
                "WHERE a.patient_id = :pid AND p.status = ANY(:paid)"
            ),
            {"pid": str(pid), "paid": _PAID_STATUSES},
        )

    async def doctor_name(self, pid: UUID) -> str | None:
        row = await fetch_optional(
            self.session,
            text(
                "SELECT dp.first_name || ' ' || dp.last_name AS name FROM doctor_patient_assignments dpa "
                "JOIN profiles dp ON dp.id = dpa.doctor_id WHERE dpa.patient_id = :pid AND dpa.status = 'active' LIMIT 1"
            ),
            {"pid": str(pid)},
        )
        return row["name"] if row else None

    # -- FAQ ---------------------------------------------------------------------

    async def faq(self, language: str) -> list[dict]:
        return await self._all(
            "SELECT faq_id, intent, category, questions, answer, reply_type, action FROM reference.assistant_faq "
            "WHERE language = :lang AND is_active ORDER BY sort_order",
            {"lang": language},
        )

    # -- consent -----------------------------------------------------------------

    async def consent(self, pid: UUID, kind: str, version: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM assistant_consents WHERE patient_id = :pid AND kind = :kind AND version = :v"),
            {"pid": str(pid), "kind": kind, "v": version},
        )

    async def accept_consent(self, pid: UUID, kind: str, version: str, language: str) -> None:
        existing = await self.consent(pid, kind, version)
        if existing is None:
            sql, params = insert_returning(
                "assistant_consents", {"patient_id": str(pid), "kind": kind, "version": version, "language": language}
            )
            await self.session.execute(sql, params)
        elif existing["withdrawn_at"] is not None:
            await self.session.execute(
                text("UPDATE assistant_consents SET withdrawn_at = NULL WHERE consent_id = :id"), {"id": str(existing["consent_id"])}
            )

    async def withdraw_consent(self, pid: UUID, kind: str) -> None:
        await self.session.execute(
            text("UPDATE assistant_consents SET withdrawn_at = now() WHERE patient_id = :pid AND kind = :kind AND withdrawn_at IS NULL"),
            {"pid": str(pid), "kind": kind},
        )

    # -- messages ----------------------------------------------------------------

    async def patient_messages_since(self, pid: UUID, since: datetime) -> int:
        row = await fetch_one(
            self.session,
            text("SELECT count(*) AS n FROM assistant_messages WHERE patient_id = :pid AND role = 'patient' AND created_at >= :since"),
            {"pid": str(pid), "since": since},
        )
        return int(row["n"])

    async def recent_turns(self, pid: UUID, conversation_id: UUID, limit: int) -> list[dict]:
        rows = await self._all(
            "SELECT role, body, answered_by FROM assistant_messages WHERE patient_id = :pid AND conversation_id = :cid "
            "ORDER BY created_at DESC LIMIT :n",
            {"pid": str(pid), "cid": str(conversation_id), "n": limit},
        )
        return list(reversed(rows))

    async def add_message(self, data: dict) -> dict:
        sql, params = insert_returning("assistant_messages", data)
        return await fetch_one(self.session, sql, params)

    async def history(self, pid: UUID, conversation_id: UUID | None, limit: int) -> list[dict]:
        where = "patient_id = :pid" + (" AND conversation_id = :cid" if conversation_id else "")
        params: dict[str, Any] = {"pid": str(pid), "n": limit}
        if conversation_id:
            params["cid"] = str(conversation_id)
        rows = await self._all(
            "SELECT message_id, conversation_id, role, body, language, answered_by, intent, safety_screen, created_at "
            f"FROM assistant_messages WHERE {where} ORDER BY created_at DESC LIMIT :n",
            params,
        )
        return list(reversed(rows))

    # -- feedback ----------------------------------------------------------------

    async def appointment_for_feedback(self, pid: UUID, appointment_id: UUID) -> dict | None:
        rows = await self._all(
            "SELECT " + _VISIT_COLUMNS + _VISIT_FROM + "WHERE a.appointment_id = :aid AND a.patient_id = :pid",
            {"aid": str(appointment_id), "pid": str(pid)},
        )
        return rows[0] if rows else None

    async def pending_feedback(self, pid: UUID, since: datetime) -> list[dict]:
        return await self._all(
            "SELECT " + _VISIT_COLUMNS + _VISIT_FROM + "WHERE a.patient_id = :pid AND a.status = 'completed' "
            "AND COALESCE(a.completed_at, a.appointment_date::timestamptz) >= :since "
            "AND NOT EXISTS (SELECT 1 FROM session_feedback f WHERE f.appointment_id = a.appointment_id) "
            "ORDER BY a.appointment_date DESC, a.start_time DESC NULLS LAST",
            {"pid": str(pid), "since": since},
        )

    async def feedback_exists(self, appointment_id: UUID) -> bool:
        row = await fetch_optional(
            self.session, text("SELECT 1 AS x FROM session_feedback WHERE appointment_id = :aid"), {"aid": str(appointment_id)}
        )
        return row is not None

    async def add_feedback(self, data: dict) -> dict:
        sql, params = insert_returning("session_feedback", data, array_columns={"side_effects", "comment_tags"})
        return await fetch_one(self.session, sql, params)


# -- AI usage (role 'system', its own short transaction) ---------------------------


async def ai_calls_since(conn: AsyncConnection, since: datetime) -> int:
    row = (
        (await conn.execute(text("SELECT count(*) AS n FROM ops.ai_usage_log WHERE created_at >= :since"), {"since": since}))
        .mappings()
        .first()
    )
    return int(row["n"]) if row else 0


async def log_ai_call(conn: AsyncConnection, data: dict) -> None:
    sql, params = insert_returning("ops.ai_usage_log", data)
    await conn.execute(sql, params)
