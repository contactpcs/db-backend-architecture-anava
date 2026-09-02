"""Data access for the Device Session module (SQL/v1/56_device_session_records.sql).

One header table (device_sessions) plus nine children. Every child reaches
its session through device_session_record_id, never appointment_id directly
— appointment_id lives only on the header row, exactly as 53 lays it out.

No DELETE anywhere: every child table's grant revokes it from anava_app (53
§12), matching every other clinical log table in this schema.
"""

from __future__ import annotations

import builtins
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning, update_returning


class DeviceSessionRepository:
    """core.device_sessions — the header, 1:1 with an appointment."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_sessions", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, device_session_record_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM device_sessions WHERE device_session_record_id = :id"),
            {"id": str(device_session_record_id)},
        )

    async def get_by_appointment(self, appointment_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM device_sessions WHERE appointment_id = :id"),
            {"id": str(appointment_id)},
        )

    async def update(self, device_session_record_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(device_session_record_id)
        sql, params = update_returning("device_sessions", "device_session_record_id", str(device_session_record_id), fields)
        return await fetch_optional(self.session, sql, params)

    async def get_pinned_device_unit_serial(self, protocol_id: UUID) -> str | None:
        """Serial of the physical unit pinned on this protocol (protocol_plan.
        device_unit_id, 73), if any — used to auto-fetch device_serial_number
        on the first checklist write instead of the CA typing it."""
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT du.serial_number FROM protocol_plan tp "
                        "JOIN device_units du ON du.device_unit_id = tp.device_unit_id "
                        "WHERE tp.protocol_id = :pid"
                    ),
                    {"pid": str(protocol_id)},
                )
            )
            .mappings()
            .first()
        )
        return row["serial_number"] if row else None

    async def update_with_now_columns(
        self, device_session_record_id: UUID, fields: dict, *, now_columns: builtins.list[str]
    ) -> dict | None:
        """Like update(), but also stamps one or more *_at columns to NOW()
        in the same statement — used by the session-status FSM (started_at,
        paused_at, resumed_at, stopped_at, completed_at), none of which
        update_returning can express since it only ever binds parameters,
        never a raw SQL expression like NOW()."""
        if not fields and not now_columns:
            return await self.get(device_session_record_id)
        set_parts = [f"{c} = NOW()" for c in now_columns]
        params: dict[str, Any] = {}
        for key, value in fields.items():
            if isinstance(value, (dict, list)):
                params[key] = json.dumps(value)
                set_parts.append(f"{key} = CAST(:{key} AS JSONB)")
            else:
                params[key] = value
                set_parts.append(f"{key} = :{key}")
        params["__id"] = str(device_session_record_id)
        sql = text(
            f"UPDATE device_sessions SET {', '.join(set_parts)}, updated_at = NOW() WHERE device_session_record_id = :__id RETURNING *"
        )
        return await fetch_optional(self.session, sql, params)


class DeviceSessionSymptomRepository:
    """core.device_session_symptoms — append-only."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_symptoms", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_symptoms WHERE device_session_record_id = :id ORDER BY recorded_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class DeviceSessionAdverseEventRepository:
    """core.device_session_adverse_events — append-only."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_adverse_events", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_adverse_events WHERE device_session_record_id = :id ORDER BY recorded_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class DeviceSessionNoteRepository:
    """core.device_session_notes — append-only. Distinct from
    core.doctor_session_notes; see 53's file header NAMING section."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_notes", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_notes WHERE device_session_record_id = :id ORDER BY recorded_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class DeviceSessionActivityRepository:
    """core.device_session_activities — append-only."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        # activities is TEXT[] (56), not JSONB — insert_returning's default
        # would CAST a JSON-serialized string to TEXT[] and fail
        # (DatatypeMismatchError). Same fix as protocol_custom_montages'
        # anode_sites/cathode_sites (sql_helpers.py).
        sql, params = insert_returning("device_session_activities", data, array_columns={"activities"})
        return await fetch_one(self.session, sql, params)

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_activities WHERE device_session_record_id = :id ORDER BY recorded_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


_SESSION_SCALE_SELECT = (
    "SELECT ss.*, ps.scale_id AS protocol_scale_scale_id, "
    "  COALESCE(pr.scale_code, ns.scale_code) AS scale_code, "
    "  COALESCE(pr.scale_name, ns.scale_name) AS scale_name "
    "FROM device_session_scales ss "
    "JOIN protocol_scales ps ON ps.protocol_scale_id = ss.protocol_scale_id "
    "LEFT JOIN reference.prs_scales pr ON pr.scale_id = ps.prs_scale_id "
    "LEFT JOIN reference.neuromod_scales ns ON ns.scale_id = ps.scale_id "
)


class DeviceSessionScaleRepository:
    """core.device_session_scales — one row per (session, protocol_scale)
    due this visit. Seeded from core.protocol_scales, then upserted as the
    CA sets delivery mode / status changes as the patient answers."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, device_session_record_id: UUID, protocol_scale_id: UUID, fields: dict) -> dict:
        """Insert-or-update-by-(session, protocol_scale) — uq_dss2_session_scale.

        Used both to seed a pending row (delivery_mode defaulted by the
        caller) and to change delivery_mode/status on an existing one.
        """
        data = {
            "device_session_record_id": str(device_session_record_id),
            "protocol_scale_id": str(protocol_scale_id),
            **fields,
        }
        cols = list(data.keys())
        set_cols = [c for c in cols if c not in ("device_session_record_id", "protocol_scale_id")]
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in set_cols) or "protocol_scale_id = EXCLUDED.protocol_scale_id"
        placeholders = ", ".join(f":{c}" for c in cols)
        sql = text(
            f"INSERT INTO device_session_scales ({', '.join(cols)}) VALUES ({placeholders}) "
            "ON CONFLICT (device_session_record_id, protocol_scale_id) "
            f"DO UPDATE SET {set_clause}, updated_at = NOW() "
            "RETURNING *"
        )
        return await fetch_one(self.session, sql, data)

    async def update_existing(self, device_session_record_id: UUID, protocol_scale_id: UUID, fields: dict) -> dict:
        """Plain UPDATE, no INSERT fallback — for callers (e.g. a patient
        completing their own scale) whose RLS role is allowed to UPDATE this
        table but not INSERT into it (rls_device_session_scales_insert is
        CA/admin/system only, see 56_device_session_records.sql). upsert()'s
        INSERT ... ON CONFLICT DO UPDATE still requires the INSERT policy to
        pass even when every call lands on the conflict branch — Postgres
        checks INSERT privilege before it knows whether a conflict occurs —
        so it 500s for those roles even though the row already exists and
        only ever needs updating. Caller must confirm the row exists first
        (e.g. via get()); this raises if it doesn't."""
        data = {
            "device_session_record_id": str(device_session_record_id),
            "protocol_scale_id": str(protocol_scale_id),
            **fields,
        }
        set_cols = [c for c in fields.keys()]
        set_clause = ", ".join(f"{c} = :{c}" for c in set_cols)
        sql = text(
            f"UPDATE device_session_scales SET {set_clause}, updated_at = NOW() "
            "WHERE device_session_record_id = :device_session_record_id "
            "AND protocol_scale_id = :protocol_scale_id "
            "RETURNING *"
        )
        return await fetch_one(self.session, sql, data)

    async def get(self, device_session_record_id: UUID, protocol_scale_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(_SESSION_SCALE_SELECT + "WHERE ss.device_session_record_id = :sid AND ss.protocol_scale_id = :psid"),
            {"sid": str(device_session_record_id), "psid": str(protocol_scale_id)},
        )

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(_SESSION_SCALE_SELECT + "WHERE ss.device_session_record_id = :id ORDER BY ps.display_order"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def complete_for_prs_instance(
        self, device_session_record_id: UUID, prs_instance_id: str, scale_ids: builtins.list[str]
    ) -> builtins.list[dict]:
        """Wires a completed PRS assessment (prs_scale_results, one row per
        scale actually scored) back onto the due-scale rows it answers —
        matched via protocol_scales.prs_scale_id, the same join
        _SESSION_SCALE_SELECT already uses to resolve a scale's real
        catalogue identity. Called once from treatment_protocols'
        record_device_session, right after that PRS instance is confirmed to
        belong to this session's patient. Previously nothing ever advanced
        prs_instance_id/status past their seeded 'pending' default."""
        if not scale_ids:
            return []
        rows = (
            (
                await self.session.execute(
                    text(
                        "UPDATE device_session_scales ss SET status = 'completed', "
                        "prs_instance_id = :instance_id, updated_at = NOW() "
                        "FROM protocol_scales ps "
                        "WHERE ss.protocol_scale_id = ps.protocol_scale_id "
                        "AND ss.device_session_record_id = :sid "
                        "AND ps.prs_scale_id = ANY(:scale_ids) "
                        "RETURNING ss.*"
                    ),
                    {"instance_id": prs_instance_id, "sid": str(device_session_record_id), "scale_ids": scale_ids},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_protocol_scales(self, protocol_id: UUID) -> builtins.list[dict]:
        """core.protocol_scales for a protocol — the source list device_session_scales
        seeds from. Minimal read against the same table
        treatment_protocols.ProtocolDetailRepository.list_scales reads,
        kept local rather than imported so this module never reaches across
        into treatment_protocols' repository internals."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT protocol_scale_id, scale_id, prs_scale_id, cadence, window_days, answered_by, display_order "
                        "FROM protocol_scales WHERE protocol_id = :id ORDER BY display_order"
                    ),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class DeviceSessionFeedbackRepository:
    """core.device_session_feedback — one row per session (uq_dsf_device_session)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_feedback", data)
        return await fetch_one(self.session, sql, params)

    async def get_for_session(self, device_session_record_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM device_session_feedback WHERE device_session_record_id = :id"),
            {"id": str(device_session_record_id)},
        )


class DeviceSessionMediaRepository:
    """core.device_session_media — attachments, gated by recording_consent_confirmed."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_media", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_media WHERE device_session_record_id = :id ORDER BY captured_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class DeviceSessionEventRepository:
    """core.device_session_events — the audit trail. Written only by the
    service layer, never from a direct client-supplied row."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_events", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_events WHERE device_session_record_id = :id ORDER BY occurred_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class DeviceSessionSosEventRepository:
    """core.device_session_sos_events — patient-raised, CA-acknowledged."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_sos_events", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, sos_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM device_session_sos_events WHERE sos_id = :id"),
            {"id": str(sos_id)},
        )

    async def list_for_session(self, device_session_record_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM device_session_sos_events WHERE device_session_record_id = :id ORDER BY raised_at"),
                    {"id": str(device_session_record_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def acknowledge(self, sos_id: UUID, *, acknowledged_by: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE device_session_sos_events SET acknowledged_by = :by, acknowledged_at = NOW() "
                "WHERE sos_id = :id AND acknowledged_at IS NULL RETURNING *"
            ),
            {"by": str(acknowledged_by), "id": str(sos_id)},
        )
