"""Data access for the Treatment Protocol module.

The catalogue is split per device family (tdcs_placements, rtms_dosing, ...)
because the parameters genuinely differ - tDCS doses in mA over minutes,
rTMS in % of resting motor threshold over pulse trains. That split is the
reason for the table-name interpolation below: the modality decides which
of six tables to read, and the slug is validated against a fixed allow-list
before it ever reaches a query string.
"""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional, insert_returning, update_returning
from app.modules.treatment_protocols.schemas import MODALITY_SLUG

# Every slug that may be interpolated into a table name. Values come from
# MODALITY_SLUG, which is itself keyed on the CHECK-constrained
# neuromod_devices.modality column - but this is asserted explicitly at each
# call site rather than trusted, so a future modality can never turn into
# SQL injection through the table-name path.
_ALLOWED_SLUGS = frozenset(MODALITY_SLUG.values())

# Per-device placement columns beyond the shared five.
_PLACEMENT_COLS = {
    "tdcs": ("anode_site", "cathode_site"),
    "hd_tdcs": ("anode_site", "return_sites"),
    "tavns": ("ear_side", "auricular_site"),
    "tps": ("target_region", "hemisphere"),
    "rtms": ("coil_target", "coil_type", "hemisphere"),
    "other": ("placement_details",),
}

_DOSING_COLS = {
    "tdcs": ("current_ma_min", "current_ma_max", "session_duration_min", "sessions_per_day"),
    "hd_tdcs": ("total_current_ma", "per_return_current_ma", "session_duration_min", "sessions_per_day"),
    "tavns": (
        "intensity_ma",
        "pulse_width_us",
        "frequency_hz",
        "duty_cycle_on_sec",
        "duty_cycle_off_sec",
        "session_duration_min",
    ),
    "tps": ("energy_mj", "pulses_per_session", "pulse_rate_hz"),
    "rtms": (
        "frequency_hz",
        "pct_motor_threshold",
        "train_count",
        "pulses_per_train",
        "pulses_per_session",
        "inter_train_interval_sec",
    ),
    "other": ("dose_details",),
}


def _assert_slug(slug: str) -> str:
    if slug not in _ALLOWED_SLUGS:
        raise ValueError(f"Unknown device modality slug: {slug!r}")
    return slug


def placement_table(slug: str) -> str:
    return f"reference.{_assert_slug(slug)}_placements"


def placement_pk(slug: str) -> str:
    return f"{_assert_slug(slug)}_placement_id"


def dosing_table(slug: str) -> str:
    return f"reference.{_assert_slug(slug)}_dosing"


def dosing_pk(slug: str) -> str:
    return f"{_assert_slug(slug)}_dosing_id"


class CatalogueRepository:
    """Steps 1-3 and the placement/dosing lookups behind steps 4-5."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- devices -----------------------------------------------------------

    async def list_companies(self, *, active_only: bool = True) -> builtins.list[dict]:
        where = "WHERE is_active = TRUE" if active_only else ""
        rows = (
            (await self.session.execute(text(f"SELECT * FROM reference.device_companies {where} ORDER BY company_name")))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_devices(self, *, phase: int | None = None, active_only: bool = True) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {}
        if active_only:
            clauses.append("d.is_active = TRUE")
        if phase is not None:
            clauses.append("d.phase = :phase")
            params["phase"] = phase
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT d.*, c.company_name, c.company_code "
                        "FROM reference.neuromod_devices d "
                        "LEFT JOIN reference.device_companies c ON c.company_id = d.company_id "
                        f"{where} ORDER BY d.phase, d.modality, d.device_name"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_device(self, device_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT d.*, c.company_name, c.company_code "
                "FROM reference.neuromod_devices d "
                "LEFT JOIN reference.device_companies c ON c.company_id = d.company_id "
                "WHERE d.device_id = :id"
            ),
            {"id": str(device_id)},
        )

    # -- conditions --------------------------------------------------------

    async def list_conditions(self, *, device_id: UUID | None = None, active_only: bool = True) -> builtins.list[dict]:
        """Conditions with their diagnosis count and best available evidence.

        When device_id is given, evidence is restricted to that device's own
        dosing rows - a condition can be evidence A on tDCS and C on rTMS,
        and the wizard shows the chip for the device actually selected.
        """
        slug_union = " UNION ALL ".join(
            f"SELECT condition_id, device_id, evidence_level FROM {dosing_table(s)} WHERE is_active = TRUE" for s in sorted(_ALLOWED_SLUGS)
        )
        params: dict[str, Any] = {}
        dev_filter = ""
        if device_id:
            dev_filter = "AND ev.device_id = :device_id"
            params["device_id"] = str(device_id)
        where = "WHERE c.is_active = TRUE" if active_only else ""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT c.*, "
                        "(SELECT count(*) FROM reference.neuromod_diagnoses d WHERE d.condition_id = c.condition_id) "
                        "  AS diagnosis_count, "
                        "(SELECT ev.evidence_level FROM (" + slug_union + ") ev "
                        f"  WHERE ev.condition_id = c.condition_id {dev_filter} "
                        "  ORDER BY CASE ev.evidence_level WHEN 'A' THEN 3 WHEN 'B' THEN 2 ELSE 1 END DESC LIMIT 1) "
                        "  AS evidence_level "
                        "FROM reference.neuromod_conditions c "
                        f"{where} ORDER BY c.display_order, c.condition_name"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_condition(self, condition_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM reference.neuromod_conditions WHERE condition_id = :id"),
            {"id": str(condition_id)},
        )

    # -- diagnoses ---------------------------------------------------------

    async def list_diagnoses(
        self,
        *,
        condition_ids: builtins.list[UUID] | None = None,
        query: str | None = None,
        skip: int = 0,
        limit: int = 200,
    ) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if condition_ids:
            # ANY(:ids) keeps this one bind param regardless of list length.
            clauses.append("d.condition_id = ANY(:ids)")
            params["ids"] = [str(c) for c in condition_ids]
        if query:
            clauses.append("(d.icd10_code ILIKE :q OR d.icd10_description ILIKE :q)")
            params["q"] = f"%{query}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT d.*, c.condition_name FROM reference.neuromod_diagnoses d "
                        "JOIN reference.neuromod_conditions c ON c.condition_id = d.condition_id "
                        f"{where} ORDER BY d.icd10_code OFFSET :skip LIMIT :limit"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_diagnoses_by_ids(self, diagnosis_ids: builtins.list[UUID]) -> builtins.list[dict]:
        if not diagnosis_ids:
            return []
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT d.*, c.condition_name FROM reference.neuromod_diagnoses d "
                        "JOIN reference.neuromod_conditions c ON c.condition_id = d.condition_id "
                        "WHERE d.diagnosis_id = ANY(:ids) ORDER BY d.icd10_code"
                    ),
                    {"ids": [str(d) for d in diagnosis_ids]},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # -- placements --------------------------------------------------------

    async def list_placements(self, slug: str, *, condition_id: UUID | None = None, device_id: UUID | None = None) -> builtins.list[dict]:
        table, pk = placement_table(slug), placement_pk(slug)
        clauses = ["p.is_active = TRUE"]
        params: dict[str, Any] = {}
        if condition_id:
            clauses.append("p.condition_id = :cid")
            params["cid"] = str(condition_id)
        if device_id:
            clauses.append("p.device_id = :did")
            params["did"] = str(device_id)
        rows = (
            (
                await self.session.execute(
                    text(
                        f"SELECT p.*, p.{pk} AS placement_id, dev.modality "
                        f"FROM {table} p "
                        "JOIN reference.neuromod_devices dev ON dev.device_id = p.device_id "
                        f"WHERE {' AND '.join(clauses)} ORDER BY p.montage_label"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_placement(self, slug: str, placement_id: UUID) -> dict | None:
        table, pk = placement_table(slug), placement_pk(slug)
        return await fetch_optional(
            self.session,
            text(
                f"SELECT p.*, p.{pk} AS placement_id, dev.modality "
                f"FROM {table} p "
                "JOIN reference.neuromod_devices dev ON dev.device_id = p.device_id "
                f"WHERE p.{pk} = :id"
            ),
            {"id": str(placement_id)},
        )

    # -- dosing ------------------------------------------------------------

    async def list_dosing(
        self,
        slug: str,
        *,
        condition_id: UUID | None = None,
        device_id: UUID | None = None,
        placement_id: UUID | None = None,
    ) -> builtins.list[dict]:
        table, pk = dosing_table(slug), dosing_pk(slug)
        place_col = placement_pk(slug)
        clauses = ["d.is_active = TRUE"]
        params: dict[str, Any] = {}
        if condition_id:
            clauses.append("d.condition_id = :cid")
            params["cid"] = str(condition_id)
        if device_id:
            clauses.append("d.device_id = :did")
            params["did"] = str(device_id)
        if placement_id:
            clauses.append(f"d.{place_col} = :pid")
            params["pid"] = str(placement_id)
        rows = (
            (
                await self.session.execute(
                    text(
                        f"SELECT d.*, d.{pk} AS dosing_id, d.{place_col} AS placement_id, dev.modality "
                        f"FROM {table} d "
                        "JOIN reference.neuromod_devices dev ON dev.device_id = d.device_id "
                        f"WHERE {' AND '.join(clauses)} "
                        "ORDER BY CASE d.evidence_level WHEN 'A' THEN 3 WHEN 'B' THEN 2 ELSE 1 END DESC"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_dosing(self, slug: str, dosing_id: UUID) -> dict | None:
        table, pk = dosing_table(slug), dosing_pk(slug)
        place_col = placement_pk(slug)
        return await fetch_optional(
            self.session,
            text(
                f"SELECT d.*, d.{pk} AS dosing_id, d.{place_col} AS placement_id, dev.modality "
                f"FROM {table} d "
                "JOIN reference.neuromod_devices dev ON dev.device_id = d.device_id "
                f"WHERE d.{pk} = :id"
            ),
            {"id": str(dosing_id)},
        )

    async def best_dosing_for_condition(self, slug: str, condition_id: UUID, device_id: UUID) -> dict | None:
        rows = await self.list_dosing(slug, condition_id=condition_id, device_id=device_id)
        return rows[0] if rows else None

    # -- scales ------------------------------------------------------------

    async def list_scales(self, *, condition_ids: builtins.list[UUID] | None = None) -> builtins.list[dict]:
        if condition_ids:
            rows = (
                (
                    await self.session.execute(
                        text(
                            "SELECT s.*, MIN(m.display_order) AS display_order "
                            "FROM reference.neuromod_scales s "
                            "JOIN reference.neuromod_condition_scales m ON m.scale_id = s.scale_id "
                            "WHERE m.condition_id = ANY(:ids) "
                            "GROUP BY s.scale_id ORDER BY display_order, s.scale_code"
                        ),
                        {"ids": [str(c) for c in condition_ids]},
                    )
                )
                .mappings()
                .all()
            )
        else:
            rows = (
                (await self.session.execute(text("SELECT s.*, 0 AS display_order FROM reference.neuromod_scales s ORDER BY s.scale_code")))
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def get_scales_by_ids(self, scale_ids: builtins.list[UUID]) -> builtins.list[dict]:
        if not scale_ids:
            return []
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM reference.neuromod_scales WHERE scale_id = ANY(:ids)"),
                    {"ids": [str(s) for s in scale_ids]},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


_PROTOCOL_SELECT = (
    "SELECT tp.*, "
    "dev.device_name, dev.modality, dc.company_name, "
    "pl.patient_id, pl.doctor_id, "
    "pp.first_name || ' ' || pp.last_name AS patient_name, "
    "dp.first_name || ' ' || dp.last_name AS doctor_name, "
    "cy.clinic_id, "
    "(SELECT count(*) FROM appointments a WHERE a.protocol_id = tp.protocol_id) AS appointment_count "
    "FROM treatment_protocols tp "
    "JOIN reference.neuromod_devices dev ON dev.device_id = tp.device_id "
    "LEFT JOIN reference.device_companies dc ON dc.company_id = dev.company_id "
    "JOIN treatment_plans pl ON pl.plan_id = tp.plan_id "
    "JOIN treatment_cycles cy ON cy.cycle_id = pl.cycle_id "
    "JOIN profiles pp ON pp.id = pl.patient_id "
    "JOIN profiles dp ON dp.id = pl.doctor_id "
)


class ProtocolRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("treatment_protocols", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, protocol_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(_PROTOCOL_SELECT + "WHERE tp.protocol_id = :id"), {"id": str(protocol_id)})

    async def list(
        self,
        *,
        plan_id: UUID | None = None,
        patient_id: UUID | None = None,
        clinic_id: UUID | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if plan_id:
            clauses.append("tp.plan_id = :plan_id")
            params["plan_id"] = str(plan_id)
        if patient_id:
            clauses.append("pl.patient_id = :patient_id")
            params["patient_id"] = str(patient_id)
        if clinic_id:
            clauses.append("cy.clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        if status:
            clauses.append("tp.status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (await self.session.execute(text(f"{_PROTOCOL_SELECT}{where} ORDER BY tp.created_at DESC OFFSET :skip LIMIT :limit"), params))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def update(self, protocol_id: UUID, fields: dict) -> dict | None:
        if not fields:
            return await self.get(protocol_id)
        sql, params = update_returning("treatment_protocols", "protocol_id", str(protocol_id), fields)
        return await fetch_optional(self.session, sql, params)

    async def set_status(self, protocol_id: UUID, status: str) -> dict | None:
        stamp = ""
        if status == "active":
            stamp = ", activated_at = COALESCE(activated_at, NOW())"
        elif status == "completed":
            stamp = ", completed_at = NOW()"
        return await fetch_optional(
            self.session,
            text(f"UPDATE treatment_protocols SET status = :status, updated_at = NOW() {stamp} WHERE protocol_id = :id RETURNING *"),
            {"status": status, "id": str(protocol_id)},
        )

    async def has_generated_appointments(self, protocol_id: UUID) -> bool:
        row = (
            (
                await self.session.execute(
                    text("SELECT 1 FROM appointments WHERE protocol_id = :id LIMIT 1"),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .first()
        )
        return row is not None

    async def plan_context(self, plan_id: UUID) -> dict | None:
        """Patient, doctor and clinic for a plan.

        treatment_plans carries no clinic_id of its own - it comes from the
        cycle, which is why fn_generate_protocol_sessions resolves it the
        same way.
        """
        return await fetch_optional(
            self.session,
            text(
                "SELECT pl.plan_id, pl.patient_id, pl.doctor_id, pl.cycle_id, pl.status AS plan_status, "
                "cy.clinic_id "
                "FROM treatment_plans pl JOIN treatment_cycles cy ON cy.cycle_id = pl.cycle_id "
                "WHERE pl.plan_id = :id"
            ),
            {"id": str(plan_id)},
        )


class ProtocolSessionRepository:
    """Protocol-born rows on the appointments spine.

    Every row written here sets plan_id. That is load-bearing, not cosmetic:
    31_appointments_payment_states.sql's hold sweeper decides delete-vs-revert
    by testing plan_id IS NULL, so a protocol-born row without it would be
    deleted on a payment timeout, taking the doctor's prescribed date with it.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("appointments", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_protocol(self, protocol_id: UUID, *, appointment_type: str | None = None) -> builtins.list[dict]:
        clauses = ["protocol_id = :id"]
        params: dict[str, Any] = {"id": str(protocol_id)}
        if appointment_type:
            clauses.append("appointment_type = :t")
            params["t"] = appointment_type
        rows = (
            (
                await self.session.execute(
                    text(
                        f"SELECT * FROM appointments WHERE {' AND '.join(clauses)} "
                        "ORDER BY appointment_date, session_number NULLS LAST, start_time NULLS LAST"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_appointment(self, appointment_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM appointments WHERE appointment_id = :id"),
            {"id": str(appointment_id)},
        )

    async def cancel_planned(self, protocol_id: UUID, *, reason: str) -> int:
        """Cancels only the not-yet-booked rows.

        A 'planned' row has a date and no time, so cancelling it releases
        nothing and strands nobody. Rows the patient has already claimed
        ('selected' onward) are left alone - those are real commitments and
        cancelling them is a separate, deliberate act.
        """
        result = await self.session.execute(
            text(
                "UPDATE appointments SET status = 'cancelled', cancellation_reason = :reason, updated_at = NOW() "
                "WHERE protocol_id = :id AND status = 'planned'"
            ),
            {"id": str(protocol_id), "reason": reason},
        )
        return result.rowcount or 0  # type: ignore[attr-defined]


class ProtocolPrsRepository:
    """The two PRS response tables.

    Kept as two tables rather than one with a discriminator, per the explicit
    requirement: a PRS taken after a device session and a PRS taken at a
    follow-up are reported on separately.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_device_session(self, data: dict) -> dict:
        sql, params = insert_returning("device_session_prs_responses", data)
        return await fetch_one(self.session, sql, params)

    async def create_follow_up(self, data: dict) -> dict:
        sql, params = insert_returning("followup_prs_responses", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_protocol(self, protocol_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT ds_prs_id AS response_id, appointment_id, protocol_id, patient_id, instance_id, "
                        "  session_number, recorded_at, 'device_session' AS kind "
                        "FROM device_session_prs_responses WHERE protocol_id = :id "
                        "UNION ALL "
                        "SELECT fu_prs_id AS response_id, appointment_id, protocol_id, patient_id, instance_id, "
                        "  after_session_number AS session_number, recorded_at, 'follow_up' AS kind "
                        "FROM followup_prs_responses WHERE protocol_id = :id "
                        "ORDER BY recorded_at"
                    ),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
