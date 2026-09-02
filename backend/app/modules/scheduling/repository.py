from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning

# appointments.patient_id and appointments.doctor_id both store profiles(id)
# directly (doctors.profile_id IS a profiles.id) — so hydrating a name is always
# a plain join on profiles.id, never a second hop through patients/doctors.
#
# The doctor join MUST be a LEFT JOIN. A device_session carries doctor_id = NULL
# by design (30 made the column nullable so a CA-run session does not occupy the
# supervising doctor's calendar, and the protocol module writes NULL there). An
# inner join silently drops every one of those rows — they would vanish from
# every list endpoint and 404 on direct fetch, with no error anywhere.
#
# Two doctor identities come back, and they answer different questions:
#   doctor_name             whose calendar this visit books. NULL for a device
#                           session — an honest answer, not missing data.
#   responsible_doctor_name which doctor owns the treatment. 58 dropped the
#                           dead appointments.plan_id -> treatment_plans.doctor_id
#                           fallback (never populated by any live code path —
#                           ProtocolInstanceService requires doctor_id, so
#                           a.doctor_id is always set in practice); this is
#                           now just an alias of a.doctor_id/dp, kept as its
#                           own field so callers don't have to know that.
_APPT_SELECT = (
    "SELECT a.*, pp.first_name || ' ' || pp.last_name AS patient_name, "
    "dp.first_name || ' ' || dp.last_name AS doctor_name, "
    # doctors.doctor_id (public ID) — /doctors/{doctor_id}/availability and
    # similar path params expect this, not a.doctor_id (profiles.id).
    "dd.doctor_id AS doctor_public_id, "
    # patients.patient_id (public ID) — GET /patients/{id} and every other
    # patient-scoped route expect this, not a.patient_id (profiles.id).
    # Same doctor_public_id gap: a.patient_id IS a profiles.id
    # (resolve.py's whole docstring is this exact mistake, found elsewhere)
    # — frontend link sites that used a.patient_id directly as a route
    # param 404'd against every patient-scoped endpoint.
    "pt.patient_id AS patient_public_id, "
    "a.doctor_id AS responsible_doctor_id, "
    "dp.first_name || ' ' || dp.last_name AS responsible_doctor_name "
    "FROM appointments a "
    "JOIN profiles pp ON pp.id = a.patient_id "
    "LEFT JOIN profiles dp ON dp.id = a.doctor_id "
    "LEFT JOIN doctors dd ON dd.profile_id = a.doctor_id "
    "LEFT JOIN patients pt ON pt.profile_id = a.patient_id "
)


class WeeklyScheduleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("doctor_weekly_schedules", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_doctor(self, doctor_id: UUID) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM doctor_weekly_schedules WHERE doctor_id = :id AND is_active = TRUE ORDER BY day_of_week"),
                    {"id": str(doctor_id)},
                )
            )
            .mappings()
            .all()
        )
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
            (await self.session.execute(text(f"SELECT * FROM doctor_schedule_overrides WHERE {clause} ORDER BY override_date"), params))
            .mappings()
            .all()
        )
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
            (
                await self.session.execute(
                    text("SELECT * FROM doctor_schedule_overrides WHERE doctor_id = :id AND override_date BETWEEN :f AND :t"),
                    {"id": str(doctor_id), "f": from_date, "t": to_date},
                )
            )
            .mappings()
            .all()
        )
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


class AppointmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointments", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, appointment_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(_APPT_SELECT + "WHERE a.appointment_id = :id"), {"id": str(appointment_id)})

    # ── patient self-service lookups ────────────────────────────────────────

    async def find_active_initial(self, patient_profile_id: UUID) -> dict | None:
        """The one live initial appointment a patient may have, if any.

        uq_one_active_initial_per_patient (31 §4) enforces this in the database
        — two concurrent taps cannot both win against a unique index, and they
        can both win against this lookup. This exists purely so the second tap
        gets a readable 409 instead of a raw constraint violation.
        """
        return await fetch_optional(
            self.session,
            text(
                _APPT_SELECT
                + "WHERE a.patient_id = :pid AND a.appointment_type = 'initial' "
                + "AND a.status IN ('selected','paid','checked_in','in_progress') LIMIT 1"
            ),
            {"pid": str(patient_profile_id)},
        )

    async def has_completed_initial(self, patient_profile_id: UUID) -> bool:
        """A follow-up needs something to follow up on."""
        row = (
            await self.session.execute(
                text(
                    "SELECT 1 FROM appointments WHERE patient_id = :pid AND appointment_type = 'initial' AND status = 'completed' LIMIT 1"
                ),
                {"pid": str(patient_profile_id)},
            )
        ).first()
        return row is not None

    async def list_for_patient(
        self, patient_profile_id: UUID, *, include_past: bool = False, statuses: builtins.list[str] | None = None
    ) -> builtins.list[dict]:
        """Every appointment of every type for one patient, protocol-generated
        'planned' rows included — those are exactly what the patient needs to
        see in order to claim a slot for them."""
        clauses = ["a.patient_id = :pid"]
        params: dict[str, Any] = {"pid": str(patient_profile_id)}
        if not include_past:
            clauses.append("a.appointment_date >= CURRENT_DATE")
        if statuses:
            clauses.append("a.status = ANY(:statuses)")
            params["statuses"] = statuses
        rows = (
            (
                await self.session.execute(
                    text(f"{_APPT_SELECT}WHERE {' AND '.join(clauses)} ORDER BY a.appointment_date, a.start_time NULLS LAST"),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list(
        self,
        *,
        clinic_id: UUID | None = None,
        region_id: UUID | None = None,
        doctor_id: UUID | None = None,
        patient_id: UUID | None = None,
        status: str | None = None,
        appointment_type: str | None = None,
        date_from=None,
        date_to=None,
        skip: int = 0,
        limit: int = 100,
    ) -> builtins.list[dict]:
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
        if appointment_type:
            # The clinical assistant's queue is device sessions only; a doctor's
            # list wants everything except them. Filtering server-side keeps the
            # caller from pulling every appointment and discarding most of it.
            clauses.append("a.appointment_type = :appointment_type")
            params["appointment_type"] = appointment_type
        if date_from:
            clauses.append("a.appointment_date >= :date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("a.appointment_date <= :date_to")
            params["date_to"] = date_to
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params["skip"], params["limit"] = skip, limit
        rows = (
            (
                await self.session.execute(
                    text(f"{_APPT_SELECT}{where} ORDER BY a.appointment_date, a.start_time OFFSET :skip LIMIT :limit"), params
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_for_doctor_on_date(self, doctor_id: UUID, on_date) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM appointments WHERE doctor_id = :id AND appointment_date = :d "
                        "AND status NOT IN ('cancelled', 'rescheduled')"
                    ),
                    {"id": str(doctor_id), "d": on_date},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_for_doctor_in_range(self, doctor_id: UUID, from_date, to_date) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM appointments WHERE doctor_id = :id AND appointment_date BETWEEN :f AND :t "
                        "AND status NOT IN ('cancelled', 'rescheduled')"
                    ),
                    {"id": str(doctor_id), "f": from_date, "t": to_date},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def update_status(
        self, appointment_id: UUID, *, status: str, cancelled_by=None, cancellation_reason=None, ca_id=None
    ) -> dict | None:
        # hold_expires_at is coupled to status by chk_appointments_hold (31 §1):
        # exactly the 'selected' rows carry an expiry and nothing else may. Any
        # transition OUT of 'selected' must therefore clear it in the same
        # statement, or the CHECK rejects the update. Clearing unconditionally
        # here is safe — this method never moves a row INTO 'selected' (that is
        # claim_slot's job, which sets both together).
        extra_cols = ", hold_expires_at = NULL"
        params: dict[str, Any] = {"status": status, "id": str(appointment_id)}
        if status == "cancelled":
            extra_cols += ", cancelled_by = :cancelled_by, cancellation_reason = :reason"
            params["cancelled_by"] = str(cancelled_by) if cancelled_by else None
            params["reason"] = cancellation_reason
        elif status == "checked_in":
            extra_cols += ", checked_in_at = NOW()"
        elif status == "in_progress":
            # Late binding: no assistant is reserved in advance. Whichever one
            # is free takes the patient, and their identity is captured at the
            # moment they start the session — this is the only place ca_id is
            # ever written for a device session.
            extra_cols += ", started_at = NOW()"
            if ca_id is not None:
                extra_cols += ", ca_id = :ca_id"
                params["ca_id"] = str(ca_id)
        elif status == "completed":
            extra_cols += ", completed_at = NOW()"
        return await fetch_optional(
            self.session,
            text(f"UPDATE appointments SET status = :status, updated_at = NOW() {extra_cols} WHERE appointment_id = :id RETURNING *"),
            params,
        )

    async def claim_slot(self, appointment_id: UUID, *, start_time, end_time, hold_expires_at, status: str) -> dict | None:
        """Puts a time on a row and moves it to 'selected' (or straight to
        'paid' when payment is bypassed).

        Time and hold are written in the SAME statement as the status, because
        three constraints all read them together:
          chk_appointments_time_pair       start and end are set together
          chk_appointments_slotted_has_time only planned/cancelled may lack a time
          chk_appointments_hold             only 'selected' carries an expiry
        Splitting this into two updates would leave an intermediate state that
        violates at least one of them.
        """
        return await fetch_optional(
            self.session,
            text(
                "UPDATE appointments SET status = :status, start_time = :start_time, "
                "end_time = :end_time, hold_expires_at = :hold, updated_at = NOW() "
                "WHERE appointment_id = :id RETURNING *"
            ),
            {
                "status": status,
                "start_time": start_time,
                "end_time": end_time,
                "hold": hold_expires_at,
                "id": str(appointment_id),
            },
        )

    async def mark_paid(self, appointment_id: UUID, *, paid_by: UUID | None = None, paid_by_role: str | None = None) -> dict | None:
        """'selected' -> 'paid'. Clears the hold: a paid slot is held forever,
        not on a timer. The WHERE clause makes this idempotent against a
        gateway webhook that delivers twice — the second call matches no row
        and returns None rather than re-running.

        Also stamps booked_by / booked_by_role, which is what those columns
        mean: whoever confirmed the booking by paying for it — the receptionist
        at the desk, the patient in the app, or 'system' for a gateway webhook.
        COALESCE so a row booked at selection time keeps its original booker
        rather than being overwritten by whoever settled the payment.
        """
        return await fetch_optional(
            self.session,
            text(
                "UPDATE appointments SET status = 'paid', hold_expires_at = NULL, updated_at = NOW(), "
                "  booked_by = COALESCE(booked_by, CAST(:paid_by AS uuid)), "
                "  booked_by_role = COALESCE(booked_by_role, :paid_by_role) "
                "WHERE appointment_id = :id AND status = 'selected' RETURNING *"
            ),
            {"id": str(appointment_id), "paid_by": str(paid_by) if paid_by else None, "paid_by_role": paid_by_role},
        )

    async def release_expired_holds(self) -> dict:
        """The sweeper's whole job, as two statements.

        Expiry is asymmetric, and the asymmetry is the point (31 header):
          no protocol_id       patient-booked. DELETED — nothing unpaid persists
                               and the patient simply books again.
          protocol_id set      protocol-born. Reverts to 'planned' with its time
                               cleared. The doctor's prescribed DATE survives;
                               only the slot is released. Deleting these would
                               destroy a prescription because of a payment
                               timeout.

        start_time and end_time are nulled together — chk_appointments_time_pair
        requires it — and hold_expires_at goes with them, since a 'planned' row
        may not carry an expiry under chk_appointments_hold.
        """
        reverted = await self.session.execute(
            text(
                "UPDATE appointments SET status = 'planned', start_time = NULL, end_time = NULL, "
                "hold_expires_at = NULL, updated_at = NOW() "
                "WHERE status = 'selected' AND hold_expires_at < NOW() "
                "AND protocol_id IS NOT NULL"
            )
        )
        deleted = await self.session.execute(
            text("DELETE FROM appointments WHERE status = 'selected' AND hold_expires_at < NOW() AND protocol_id IS NULL")
        )
        return {
            "reverted_to_planned": reverted.rowcount or 0,  # type: ignore[attr-defined]
            "deleted": deleted.rowcount or 0,  # type: ignore[attr-defined]
        }

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


class AppointmentAuditLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointment_audit_logs", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_appointment(self, appointment_id: UUID) -> list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM appointment_audit_logs WHERE appointment_id = :id ORDER BY changed_at"),
                    {"id": str(appointment_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class ClinicDeviceScheduleRepository:
    """The pool a device_session books against (36_clinic_device_schedules.sql,
    re-keyed to one pool per device by 41_device_capacity_per_device.sql).

    Deliberately the same shape as WeeklyScheduleRepository /
    ScheduleOverrideRepository, because AvailabilityService's slot builder is
    reused for both — it takes a schedule and emits slots, and does not care
    whose schedule it was. The scoping key is now clinic_device_id, not
    clinic_id — clinic_id is still carried on both tables (for RLS) and still
    accepted here where a query needs it, but a device's week competes only
    against that same device's bookings, never another device's.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_device(self, clinic_device_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM clinic_device_schedules WHERE clinic_device_id = :id AND is_active = TRUE ORDER BY day_of_week"),
                    {"id": str(clinic_device_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_for_devices(self, clinic_device_ids: builtins.list[UUID]) -> dict[UUID, builtins.list[dict]]:
        """Every active weekly row for a set of devices, grouped by device —
        one query for the clinic overview screen instead of one per device."""
        if not clinic_device_ids:
            return {}
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM clinic_device_schedules "
                        "WHERE clinic_device_id = ANY(:ids) AND is_active = TRUE "
                        "ORDER BY clinic_device_id, day_of_week"
                    ),
                    {"ids": [str(i) for i in clinic_device_ids]},
                )
            )
            .mappings()
            .all()
        )
        out: dict[UUID, builtins.list[dict]] = {i: [] for i in clinic_device_ids}
        for r in rows:
            out.setdefault(r["clinic_device_id"], []).append(dict(r))
        return out

    async def replace_for_device(
        self, clinic_device_id: UUID, clinic_id: UUID, items: builtins.list[dict], *, created_by: UUID
    ) -> builtins.list[dict]:
        """Atomic delete-then-insert of one device's whole week.

        Matches how a doctor redraws their own timetable: there is no natural
        per-day PATCH when the admin is editing the entire week in one form.
        """
        await self.session.execute(text("DELETE FROM clinic_device_schedules WHERE clinic_device_id = :id"), {"id": str(clinic_device_id)})
        out: builtins.list[dict] = []
        for item in items:
            sql, params = insert_returning(
                "clinic_device_schedules",
                {**item, "clinic_device_id": str(clinic_device_id), "clinic_id": str(clinic_id), "created_by": str(created_by)},
            )
            out.append(await fetch_one(self.session, sql, params))
        return out

    async def overrides_for_range(self, clinic_device_id: UUID, from_date, to_date) -> dict:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM clinic_device_schedule_overrides WHERE clinic_device_id = :id AND override_date BETWEEN :f AND :t"),
                    {"id": str(clinic_device_id), "f": from_date, "t": to_date},
                )
            )
            .mappings()
            .all()
        )
        return {r["override_date"]: dict(r) for r in rows}

    async def list_overrides(self, clinic_device_id: UUID, *, from_date=None) -> builtins.list[dict]:
        clause = "clinic_device_id = :id"
        params: dict[str, Any] = {"id": str(clinic_device_id)}
        if from_date:
            clause += " AND override_date >= :f"
            params["f"] = from_date
        rows = (
            (
                await self.session.execute(
                    text(f"SELECT * FROM clinic_device_schedule_overrides WHERE {clause} ORDER BY override_date"), params
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def create_override(self, data: dict) -> dict:
        sql, params = insert_returning("clinic_device_schedule_overrides", data)
        return await fetch_one(self.session, sql, params)

    async def get_override(self, override_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM clinic_device_schedule_overrides WHERE override_id = :id"),
            {"id": str(override_id)},
        )

    async def delete_override(self, override_id: UUID) -> bool:
        result = await self.session.execute(
            text("DELETE FROM clinic_device_schedule_overrides WHERE override_id = :id"), {"id": str(override_id)}
        )
        return result.rowcount > 0  # type: ignore[attr-defined]

    # ── capacity ────────────────────────────────────────────────────────────
    #
    # No FOR UPDATE row lock here — clinic_device_schedules' RLS correctly
    # denies patients UPDATE-level access, and a locking SELECT in Postgres
    # requires satisfying the UPDATE policy too, not just SELECT, so a
    # patient-initiated claim could never take one on this table. The
    # concurrency lock lives in DeviceCapacityService.reserve() instead, as
    # a pg_advisory_xact_lock (not a row write, RLS doesn't apply to it).

    async def count_overlapping_sessions(self, clinic_device_id: UUID, on_date, start_time, end_time) -> int:
        """How many sessions already occupy any part of [start_time, end_time)
        on THIS device — a real time-range overlap count, not an exact-
        start_time match, since sessions now vary in length
        (billable_items.duration_minutes) instead of a fixed device chunk.

        Counts only slot-occupying statuses: a 'planned' row has no time and
        occupies nothing, and cancelled/rescheduled/completed rows have let go.
        Backed by idx_appointments_device_capacity.
        """
        return (
            await self.session.execute(
                text(
                    "SELECT count(*) FROM appointments "
                    "WHERE clinic_device_id = :did AND appointment_type = 'device_session' "
                    "AND appointment_date = :d AND start_time < :end_time AND end_time > :start_time "
                    "AND status IN ('selected','paid','checked_in','in_progress')"
                ),
                {"did": str(clinic_device_id), "d": on_date, "start_time": start_time, "end_time": end_time},
            )
        ).scalar_one()

    async def booked_intervals_for_day(self, clinic_device_id: UUID, on_date) -> builtins.list[dict]:
        """Every occupied [start_time, end_time) window on this device for
        one day — the raw data a continuous availability timeline is built
        from, as opposed to booked_counts_for_range's per-slot counts."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT start_time, end_time FROM appointments "
                        "WHERE clinic_device_id = :did AND appointment_type = 'device_session' "
                        "AND appointment_date = :d AND start_time IS NOT NULL "
                        "AND status IN ('selected','paid','checked_in','in_progress') "
                        "ORDER BY start_time"
                    ),
                    {"did": str(clinic_device_id), "d": on_date},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def booked_counts_for_range(self, clinic_device_id: UUID, from_date, to_date) -> dict:
        """(date, start_time) -> how many sessions on THIS device are booked.

        One query for the whole range, so building a month of availability does
        not fire a count per slot.
        """
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT appointment_date, start_time, count(*) AS booked FROM appointments "
                        "WHERE clinic_device_id = :did AND appointment_type = 'device_session' "
                        "AND appointment_date BETWEEN :f AND :t AND start_time IS NOT NULL "
                        "AND status IN ('selected','paid','checked_in','in_progress') "
                        "GROUP BY appointment_date, start_time"
                    ),
                    {"did": str(clinic_device_id), "f": from_date, "t": to_date},
                )
            )
            .mappings()
            .all()
        )
        return {(r["appointment_date"], r["start_time"]): r["booked"] for r in rows}


class ClinicDeviceRepository:
    """Which device types a clinic owns (37_clinic_devices.sql).

    Feeds the protocol picker at Step 1 — protocol_plan joins this table
    when a clinic_id is supplied — and is independently enforced by
    trg_check_device_available_at_clinic, so filtering here is a convenience,
    never the guarantee.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_clinic(self, clinic_id: UUID, *, active_only: bool = True) -> builtins.list[dict]:
        clause = "cd.clinic_id = :cid"
        if active_only:
            clause += " AND cd.is_active"
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT cd.*, d.device_code, d.device_name, d.modality, d.phase, "
                        "       d.is_active AS device_is_active, c.company_name "
                        "FROM clinic_devices cd "
                        "JOIN reference.neuromod_devices d ON d.device_id = cd.device_id "
                        "LEFT JOIN reference.device_companies c ON c.company_id = d.company_id "
                        f"WHERE {clause} "
                        "ORDER BY d.modality, d.device_name"
                    ),
                    {"cid": str(clinic_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get(self, clinic_device_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM clinic_devices WHERE clinic_device_id = :id"),
            {"id": str(clinic_device_id)},
        )

    async def find(self, clinic_id: UUID, device_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM clinic_devices WHERE clinic_id = :cid AND device_id = :did"),
            {"cid": str(clinic_id), "did": str(device_id)},
        )

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("clinic_devices", data)
        return await fetch_one(self.session, sql, params)

    async def update(self, clinic_device_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(clinic_device_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        return await fetch_optional(
            self.session,
            text(f"UPDATE clinic_devices SET {set_clause}, updated_at = NOW() WHERE clinic_device_id = :id RETURNING *"),
            {**fields, "id": str(clinic_device_id)},
        )

    async def delete(self, clinic_device_id: UUID) -> bool:
        result = await self.session.execute(text("DELETE FROM clinic_devices WHERE clinic_device_id = :id"), {"id": str(clinic_device_id)})
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def is_used_by_a_protocol(self, clinic_id: UUID, device_id: UUID) -> bool:
        """Has any protocol at this clinic ever prescribed this device?

        If so the inventory row should be deactivated rather than deleted —
        deleting it would leave a historic protocol pointing at a device the
        clinic has no record of owning.

        Joins through protocol_instances, not plan_id (47) — protocol_plan.
        plan_id is optional, and the old plan_id-only join silently missed
        every instance-parented protocol, the same failure class 45 hunted
        for RLS. protocol_instances carries clinic_id directly since 58 — no
        more joining out to treatment_cycles.
        """
        row = (
            await self.session.execute(
                text(
                    "SELECT 1 FROM protocol_plan tp "
                    "JOIN protocol_instances pi ON pi.instance_id = tp.instance_id "
                    "WHERE pi.clinic_id = :cid AND tp.device_id = :did LIMIT 1"
                ),
                {"cid": str(clinic_id), "did": str(device_id)},
            )
        ).first()
        return row is not None


class DeviceUnitRepository:
    """Serial-numbered physical units under a clinic_devices row (73_device_units.sql)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_clinic_device(self, clinic_device_id: UUID, *, active_only: bool = True) -> builtins.list[dict]:
        clause = "clinic_device_id = :cdid"
        if active_only:
            clause += " AND status = 'active'"
        rows = (
            (
                await self.session.execute(
                    text(f"SELECT * FROM device_units WHERE {clause} ORDER BY serial_number"),
                    {"cdid": str(clinic_device_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get(self, device_unit_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM device_units WHERE device_unit_id = :id"),
            {"id": str(device_unit_id)},
        )

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("device_units", data)
        return await fetch_one(self.session, sql, params)

    async def update(self, device_unit_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(device_unit_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        return await fetch_optional(
            self.session,
            text(f"UPDATE device_units SET {set_clause}, updated_at = NOW() WHERE device_unit_id = :id RETURNING *"),
            {**fields, "id": str(device_unit_id)},
        )

    async def delete(self, device_unit_id: UUID) -> bool:
        result = await self.session.execute(text("DELETE FROM device_units WHERE device_unit_id = :id"), {"id": str(device_unit_id)})
        return result.rowcount > 0  # type: ignore[attr-defined]
