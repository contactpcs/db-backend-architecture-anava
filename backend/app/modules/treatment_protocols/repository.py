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
            (await self.session.execute(text(f"SELECT * FROM reference.device_companies {where} ORDER BY company_name"))).mappings().all()
        )
        return [dict(r) for r in rows]

    async def list_devices(
        self, *, phase: int | None = None, active_only: bool = True, clinic_id: UUID | None = None
    ) -> builtins.list[dict]:
        """The Step 1 device picker.

        clinic_id narrows the catalogue to what that clinic actually owns
        (core.clinic_devices, 37). Without it this returns the whole registry,
        which is right for a superadmin managing the catalogue and wrong for a
        doctor prescribing — they would be offered machines that are not in the
        building.

        An INNER join, not a LEFT one: a device the clinic does not have must
        disappear from the list, not appear with a null count. The same rule is
        enforced independently by trg_check_device_available_at_clinic, so a
        stale tab or a direct API call cannot get past it either.
        """
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {}
        if active_only:
            clauses.append("d.is_active = TRUE")
        if phase is not None:
            clauses.append("d.phase = :phase")
            params["phase"] = phase

        clinic_join = ""
        clinic_cols = ""
        if clinic_id is not None:
            clinic_join = (
                "JOIN core.clinic_devices cd ON cd.device_id = d.device_id "
                "AND cd.clinic_id = :clinic_id AND cd.is_active AND cd.quantity > 0 "
            )
            # How many the clinic owns, so the picker can show it. Distinct from
            # clinic_device_schedules.capacity, which is how many sessions may
            # run at once and also depends on assistants on shift.
            clinic_cols = ", cd.quantity AS clinic_quantity"
            params["clinic_id"] = str(clinic_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (
                await self.session.execute(
                    text(
                        f"SELECT d.*, c.company_name, c.company_code{clinic_cols} "
                        "FROM reference.neuromod_devices d "
                        "LEFT JOIN reference.device_companies c ON c.company_id = d.company_id "
                        f"{clinic_join}"
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

    async def get_conditions_by_ids(self, condition_ids: builtins.list[UUID]) -> builtins.list[dict]:
        if not condition_ids:
            return []
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM reference.neuromod_conditions WHERE condition_id = ANY(:ids)"),
                    {"ids": [str(c) for c in condition_ids]},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

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
        """Step 6's scale list, sourced from the PRS catalogue (51).

        reference.prs_scales is what the questionnaire engine renders from, so
        it is the only catalogue a protocol can usefully prescribe against.
        The previous source, reference.neuromod_scales, overlapped it by 5 of
        13 rows — PHQ-9, the default depression instrument, was never in PRS at
        all, so every protocol prescribing it queued a questionnaire that could
        not be rendered.

        The join goes condition -> PRS disease -> scale, at the DISEASE level:
        neuromod_condition_prs_diseases is the bridge, because the two
        catalogues correspond as diseases and not as individual scale codes.
        """
        if condition_ids:
            rows = (
                (
                    await self.session.execute(
                        text(
                            "SELECT s.scale_id, s.scale_code, s.scale_name, "
                            "  s.is_common_scale, s.applicable_for, "
                            "  MIN(dm.display_order) AS display_order, "
                            "  bool_or(dm.is_required) AS is_required "
                            "FROM reference.neuromod_condition_prs_diseases m "
                            "JOIN reference.prs_disease_scale_map dm ON dm.disease_id = m.disease_id "
                            "JOIN reference.prs_scales s ON s.scale_id = dm.scale_id "
                            "WHERE m.condition_id = ANY(:ids) "
                            "GROUP BY s.scale_id, s.scale_code, s.scale_name, s.is_common_scale, s.applicable_for "
                            "ORDER BY display_order, s.scale_code"
                        ),
                        {"ids": [str(c) for c in condition_ids]},
                    )
                )
                .mappings()
                .all()
            )
        else:
            rows = (
                (
                    await self.session.execute(
                        text(
                            "SELECT s.scale_id, s.scale_code, s.scale_name, "
                            "  s.is_common_scale, s.applicable_for, 0 AS display_order, FALSE AS is_required "
                            "FROM reference.prs_scales s ORDER BY s.scale_code"
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def get_prs_scales_by_ids(self, prs_scale_ids: builtins.list[str]) -> builtins.list[dict]:
        """Existence check for step 6 ids, against the PRS catalogue (51)."""
        if not prs_scale_ids:
            return []
        rows = (
            (
                await self.session.execute(
                    text("SELECT scale_id AS prs_scale_id, scale_code, scale_name FROM reference.prs_scales WHERE scale_id = ANY(:ids)"),
                    {"ids": [str(s) for s in prs_scale_ids]},
                )
            )
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


# 47 renamed treatment_protocols to protocol_plan and made instance_id NOT
# NULL — every row has exactly one protocol_instances parent now. 48 dropped
# plan_id entirely: protocol_plan no longer references treatment_plans at
# all, protocol_instances is its only parent. 58 retired treatment_cycles —
# patient/doctor/clinic live on protocol_instances directly, no more join.
_PROTOCOL_SELECT = (
    "SELECT tp.*, "
    "dev.device_name, dev.modality, dc.company_name, "
    "du.serial_number AS device_unit_serial_number, "
    "pi.patient_id, pi.doctor_id, "
    "pp.first_name || ' ' || pp.last_name AS patient_name, "
    "dp.first_name || ' ' || dp.last_name AS doctor_name, "
    # patients.patient_id (public ID) — GET /patients/{id} and every other
    # patient-scoped route expect this, not pi.patient_id above
    # (protocol_instances.patient_id is profiles.id). Same doctor_public_id/
    # appointments.patient_public_id gap: frontend link sites that used
    # protocol.patient_id directly as a route param 404'd against every
    # patient-scoped endpoint.
    "pat.patient_id AS patient_public_id, "
    "pi.clinic_id, "
    "pi.instance_number, pi.status AS instance_status, "
    "(SELECT count(*) FROM appointments a WHERE a.protocol_id = tp.protocol_id) AS appointment_count "
    "FROM protocol_plan tp "
    "JOIN reference.neuromod_devices dev ON dev.device_id = tp.device_id "
    "LEFT JOIN reference.device_companies dc ON dc.company_id = dev.company_id "
    "LEFT JOIN device_units du ON du.device_unit_id = tp.device_unit_id "
    "JOIN protocol_instances pi ON pi.instance_id = tp.instance_id "
    "LEFT JOIN profiles pp ON pp.id = pi.patient_id "
    "LEFT JOIN profiles dp ON dp.id = pi.doctor_id "
    "LEFT JOIN patients pat ON pat.profile_id = pi.patient_id "
)


class ProtocolRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("protocol_plan", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, protocol_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text(_PROTOCOL_SELECT + "WHERE tp.protocol_id = :id"), {"id": str(protocol_id)})

    async def get_latest_as_of(self, instance_id: UUID, cutoff_date) -> dict | None:
        """The protocol version that was current as of a given visit date —
        inherited by a later follow-up that hasn't amended or replaced it.
        Whatever version was live then (root or an amendment), not
        necessarily the one authored at this instance's first visit."""
        return await fetch_optional(
            self.session,
            text(f"{_PROTOCOL_SELECT}WHERE tp.instance_id = :iid AND tp.created_at::date <= :cutoff ORDER BY tp.created_at DESC LIMIT 1"),
            {"iid": str(instance_id), "cutoff": cutoff_date},
        )

    async def list(
        self,
        *,
        instance_id: UUID | None = None,
        patient_id: UUID | None = None,
        clinic_id: UUID | None = None,
        status: str | None = None,
        authored_in_appointment_id: UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if instance_id:
            clauses.append("tp.instance_id = :instance_id")
            params["instance_id"] = str(instance_id)
        if patient_id:
            clauses.append("pi.patient_id = :patient_id")
            params["patient_id"] = str(patient_id)
        if clinic_id:
            clauses.append("pi.clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        if status:
            clauses.append("tp.status = :status")
            params["status"] = status
        if authored_in_appointment_id:
            clauses.append("tp.authored_in_appointment_id = :authored_in_appointment_id")
            params["authored_in_appointment_id"] = str(authored_in_appointment_id)
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
        sql, params = update_returning("protocol_plan", "protocol_id", str(protocol_id), fields)
        return await fetch_optional(self.session, sql, params)

    async def set_status(self, protocol_id: UUID, status: str) -> dict | None:
        stamp = ""
        if status == "active":
            stamp = ", activated_at = COALESCE(activated_at, NOW())"
        elif status == "completed":
            stamp = ", completed_at = NOW()"
        return await fetch_optional(
            self.session,
            text(f"UPDATE protocol_plan SET status = :status, updated_at = NOW() {stamp} WHERE protocol_id = :id RETURNING *"),
            {"status": status, "id": str(protocol_id)},
        )

    async def next_major_version(self, instance_id: UUID) -> int:
        """Next lineage number within this instance — one past however many
        protocols have been authored here with no supersedes_protocol_id
        (each such row starts a new lineage; amendments inherit their
        target's major instead of consuming one)."""
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT COALESCE(MAX(version_major), 0) + 1 AS n FROM protocol_plan "
                        "WHERE instance_id = :i AND supersedes_protocol_id IS NULL"
                    ),
                    {"i": str(instance_id)},
                )
            )
            .mappings()
            .first()
        )
        return int(row["n"]) if row else 1

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

    async def clinic_has_device(self, clinic_id: UUID, device_id: UUID) -> bool:
        """Does this clinic own a working unit of this device? (37)

        quantity > 0 as well as is_active: a clinic row that exists with zero
        units is a machine that was returned, and prescribing onto it books
        sessions nobody can run.
        """
        row = (
            (
                await self.session.execute(
                    text("SELECT 1 FROM clinic_devices WHERE clinic_id = :cid AND device_id = :did AND is_active AND quantity > 0 LIMIT 1"),
                    {"cid": str(clinic_id), "did": str(device_id)},
                )
            )
            .mappings()
            .first()
        )
        return row is not None

    async def device_unit_belongs_to_clinic_device(self, clinic_id: UUID, device_id: UUID, device_unit_id: UUID) -> bool:
        """Does device_unit_id (73) sit under THIS clinic's row for device_id?

        Guards against a doctor pinning a unit that belongs to a different
        clinic's inventory (or a different device type entirely) via a
        stale tab or a direct API call.
        """
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT 1 FROM device_units du "
                        "JOIN clinic_devices cd ON cd.clinic_device_id = du.clinic_device_id "
                        "WHERE du.device_unit_id = :uid AND cd.clinic_id = :cid AND cd.device_id = :did "
                        "AND du.status = 'active' LIMIT 1"
                    ),
                    {"uid": str(device_unit_id), "cid": str(clinic_id), "did": str(device_id)},
                )
            )
            .mappings()
            .first()
        )
        return row is not None

    async def instance_context(self, instance_id: UUID) -> dict | None:
        """Patient, doctor and clinic for a protocol instance (58).

        The instance is the protocol's only parent and carries its own
        patient/doctor/clinic directly — no more joining out to a cycle. Kept
        identical to fn_check_device_available_at_clinic's resolution so the
        pre-flight check and the trigger cannot disagree.
        """
        return await fetch_optional(
            self.session,
            text(
                "SELECT instance_id, patient_id, doctor_id, ca_id, clinic_id, "
                "  created_by, instance_number, status AS instance_status "
                "FROM protocol_instances "
                "WHERE instance_id = :id"
            ),
            {"id": str(instance_id)},
        )

    async def clinic_device_for(self, clinic_id: UUID, device_id: UUID) -> UUID | None:
        """Which of the clinic's units a device_session books against.

        41 added appointments.clinic_device_id and made it mandatory for every
        device_session row (chk_appointments_device_session_has_device is a
        biconditional: device sessions must have one, everything else must
        not). Resolved exactly the way 41's own fn_generate_protocol_sessions
        resolves it, so the SQL and application generators agree.
        """
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT clinic_device_id FROM clinic_devices "
                        "WHERE clinic_id = :cid AND device_id = :did AND is_active AND quantity > 0 "
                        "ORDER BY created_at LIMIT 1"
                    ),
                    {"cid": str(clinic_id), "did": str(device_id)},
                )
            )
            .mappings()
            .first()
        )
        return row["clinic_device_id"] if row else None


class ProtocolInstanceRepository:
    """core.protocol_instances (45) — one course of device treatment.

    The protocol's real parent. Distinct from treatment_plans, which is the
    clinical picture (anamnesis, history, assessments) rather than the device
    prescription; 45's header spells out why the two had to stop being the
    same row.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("protocol_instances", data)
        return await fetch_one(self.session, sql, params)

    async def get(self, instance_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "SELECT pi.*, "
                "  pp.first_name || ' ' || pp.last_name AS patient_name, "
                "  cp.first_name || ' ' || cp.last_name AS created_by_name, "
                "  (SELECT count(*) FROM protocol_plan x WHERE x.instance_id = pi.instance_id) AS protocol_count "
                "FROM protocol_instances pi "
                "LEFT JOIN profiles pp ON pp.id = pi.patient_id "
                "LEFT JOIN profiles cp ON cp.id = pi.created_by "
                "WHERE pi.instance_id = :id"
            ),
            {"id": str(instance_id)},
        )

    async def list(
        self,
        *,
        patient_id: UUID | None = None,
        clinic_id: UUID | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if patient_id:
            clauses.append("pi.patient_id = :patient_id")
            params["patient_id"] = str(patient_id)
        if clinic_id:
            clauses.append("pi.clinic_id = :clinic_id")
            params["clinic_id"] = str(clinic_id)
        if status:
            clauses.append("pi.status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT pi.*, "
                        "  pp.first_name || ' ' || pp.last_name AS patient_name, "
                        "  cp.first_name || ' ' || cp.last_name AS created_by_name, "
                        "  (SELECT count(*) FROM protocol_plan x WHERE x.instance_id = pi.instance_id) AS protocol_count "
                        "FROM protocol_instances pi "
                        "LEFT JOIN profiles pp ON pp.id = pi.patient_id "
                        "LEFT JOIN profiles cp ON cp.id = pi.created_by "
                        f"{where} ORDER BY pi.created_at DESC OFFSET :skip LIMIT :limit"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def next_instance_number(self, patient_id: UUID) -> int:
        row = (
            (
                await self.session.execute(
                    text("SELECT COALESCE(MAX(instance_number), 0) + 1 AS n FROM protocol_instances WHERE patient_id = :p"),
                    {"p": str(patient_id)},
                )
            )
            .mappings()
            .first()
        )
        return int(row["n"]) if row else 1

    async def get_open_for_patient(self, patient_id: UUID) -> dict | None:
        """The live instance for a patient, if any.

        uq_protocol_instances_one_active (45, repointed to patient_id by 58)
        allows at most one row in draft or active per patient, so this
        returns zero or one — the check the service makes before opening
        another episode.
        """
        return await fetch_optional(
            self.session,
            text("SELECT * FROM protocol_instances WHERE patient_id = :p AND status IN ('draft', 'active') LIMIT 1"),
            {"p": str(patient_id)},
        )

    async def set_status(self, instance_id: UUID, status: str) -> dict | None:
        return await fetch_optional(
            self.session,
            text("UPDATE protocol_instances SET status = :s, updated_at = NOW() WHERE instance_id = :id RETURNING *"),
            {"s": status, "id": str(instance_id)},
        )


class ProtocolDetailRepository:
    """The four child tables 38 added: conditions, diagnoses, scales and
    doctor-authored montages.

    Every one of these was being accepted by the API and discarded before
    reaching a table. They are written inside the same transaction as the
    protocol row, so a protocol can never exist without the diagnosis that
    justifies it.

    No DELETE anywhere: 32's grants revoke it from anava_app across the whole
    module, and a prescribing record is not something a request rewrites.
    Amending a protocol means issuing a new one (supersedes_protocol_id, 39).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- conditions (step 2) -----------------------------------------------

    async def add_conditions(self, protocol_id: UUID, items: builtins.list[dict]) -> int:
        """items: [{"condition_id": UUID|None, "other_text": str|None}]

        chk_protocol_conditions_shape requires exactly one of the two per row;
        the request model has already enforced that.
        """
        written = 0
        for item in items:
            await self.session.execute(
                text(
                    "INSERT INTO protocol_conditions (protocol_id, condition_id, other_text) "
                    "VALUES (:pid, :cid, :other) "
                    # uq_protocol_conditions_catalogue is partial (condition_id
                    # IS NOT NULL), so the same free-text description twice is
                    # legitimately two rows and is not deduplicated here.
                    "ON CONFLICT (protocol_id, condition_id) "
                    "WHERE condition_id IS NOT NULL DO NOTHING"
                ),
                {
                    "pid": str(protocol_id),
                    "cid": str(item["condition_id"]) if item.get("condition_id") else None,
                    "other": item.get("other_text"),
                },
            )
            written += 1
        return written

    async def list_conditions(self, protocol_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT pc.protocol_condition_id, pc.condition_id, pc.other_text, "
                        "  c.condition_name "
                        "FROM protocol_conditions pc "
                        "LEFT JOIN reference.neuromod_conditions c ON c.condition_id = pc.condition_id "
                        "WHERE pc.protocol_id = :id ORDER BY c.display_order NULLS LAST, pc.created_at"
                    ),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # -- diagnoses (step 3) ------------------------------------------------

    async def add_diagnoses(self, protocol_id: UUID, diagnosis_ids: builtins.list[UUID]) -> int:
        if not diagnosis_ids:
            return 0
        result = await self.session.execute(
            text(
                "INSERT INTO protocol_diagnoses (protocol_id, diagnosis_id) "
                "SELECT :pid, unnest(CAST(:ids AS uuid[])) "
                "ON CONFLICT (protocol_id, diagnosis_id) DO NOTHING"
            ),
            {"pid": str(protocol_id), "ids": [str(d) for d in diagnosis_ids]},
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def list_diagnoses(self, protocol_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT pd.protocol_diagnosis_id, pd.diagnosis_id, "
                        "  d.icd10_code, d.icd10_description, c.condition_name "
                        "FROM protocol_diagnoses pd "
                        "JOIN reference.neuromod_diagnoses d ON d.diagnosis_id = pd.diagnosis_id "
                        "JOIN reference.neuromod_conditions c ON c.condition_id = d.condition_id "
                        "WHERE pd.protocol_id = :id ORDER BY d.icd10_code"
                    ),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # -- scales (step 6) ---------------------------------------------------

    async def add_scales(self, protocol_id: UUID, items: builtins.list[dict]) -> int:
        """items carry an already-normalised cadence - see
        schemas.normalise_cadence. Sending a raw UI label here raises 23514
        on chk_protocol_scales_cadence.
        """
        written = 0
        for item in items:
            result = await self.session.execute(
                text(
                    "INSERT INTO protocol_scales "
                    "  (protocol_id, prs_scale_id, cadence, window_days, answered_by, display_order) "
                    "VALUES (:pid, :sid, :cadence, :window, :answered_by, :ord) "
                    # A scale appears once per protocol; twice would release the
                    # same questionnaire on the same trigger. Inferred against
                    # uq_protocol_scales_prs (51) — the pre-51 UNIQUE was on
                    # scale_id, which PRS-sourced rows leave NULL.
                    "ON CONFLICT (protocol_id, prs_scale_id) "
                    "WHERE prs_scale_id IS NOT NULL DO NOTHING"
                ),
                {
                    "pid": str(protocol_id),
                    "sid": str(item["scale_id"]),
                    "cadence": item["cadence"],
                    "window": item.get("window_days"),
                    "answered_by": item.get("answered_by", "patient"),
                    "ord": item.get("display_order", 0),
                },
            )
            written += result.rowcount or 0  # type: ignore[attr-defined]
        return written

    async def list_scales(self, protocol_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text(
                        # LEFT JOINs and COALESCE because a row carries one catalogue
                        # or the other (chk_protocol_scales_one_catalogue, 51): PRS for
                        # anything written since, neuromod_scales for rows written before.
                        "SELECT ps.protocol_scale_id, ps.scale_id, ps.prs_scale_id, "
                        "  ps.cadence, ps.window_days, ps.answered_by, ps.display_order, "
                        "  COALESCE(pr.scale_code, ns.scale_code) AS scale_code, "
                        "  COALESCE(pr.scale_name, ns.scale_name) AS scale_name "
                        "FROM protocol_scales ps "
                        "LEFT JOIN reference.prs_scales pr      ON pr.scale_id = ps.prs_scale_id "
                        "LEFT JOIN reference.neuromod_scales ns ON ns.scale_id = ps.scale_id "
                        "WHERE ps.protocol_id = :id "
                        "ORDER BY ps.display_order, COALESCE(pr.scale_code, ns.scale_code)"
                    ),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


_MONTAGE_SELECT = (
    "SELECT m.*, dev.device_name, dev.modality, c.condition_name, "
    "  p.first_name || ' ' || p.last_name AS created_by_name "
    "FROM protocol_custom_montages m "
    "JOIN reference.neuromod_devices dev ON dev.device_id = m.device_id "
    "LEFT JOIN reference.neuromod_conditions c ON c.condition_id = m.condition_id "
    "LEFT JOIN profiles p ON p.id = m.created_by "
)


class CustomMontageRepository:
    """core.protocol_custom_montages (38).

    Separate from CatalogueRepository on purpose: that class reads the curated
    reference library, this one writes doctor-authored placements into core.
    Merging them would blur exactly the line 38 created the table to draw.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        # anode_sites/cathode_sites are TEXT[] columns (38), not JSONB —
        # insert_returning's default would CAST a JSON-serialized string to
        # TEXT[] and fail (DatatypeMismatchError). See sql_helpers.py.
        sql, params = insert_returning("protocol_custom_montages", data, array_columns={"anode_sites", "cathode_sites"})
        return await fetch_one(self.session, sql, params)

    async def get(self, custom_montage_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text(_MONTAGE_SELECT + "WHERE m.custom_montage_id = :id"),
            {"id": str(custom_montage_id)},
        )

    async def list(
        self,
        *,
        created_by: UUID | None = None,
        device_id: UUID | None = None,
        condition_id: UUID | None = None,
        clinic_id: UUID | None = None,
        active_only: bool = True,
    ) -> builtins.list[dict]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {}
        if active_only:
            clauses.append("m.is_active")
        if created_by:
            clauses.append("m.created_by = :created_by")
            params["created_by"] = str(created_by)
        if device_id:
            clauses.append("m.device_id = :device_id")
            params["device_id"] = str(device_id)
        if condition_id:
            # A general-purpose montage (condition_id NULL) is offered for
            # every condition, so it must not be filtered out here.
            clauses.append("(m.condition_id = :condition_id OR m.condition_id IS NULL)")
            params["condition_id"] = str(condition_id)
        if clinic_id:
            clauses.append("(m.clinic_id = :clinic_id OR m.clinic_id IS NULL)")
            params["clinic_id"] = str(clinic_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (await self.session.execute(text(f"{_MONTAGE_SELECT}{where} ORDER BY m.created_at DESC"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def deactivate(self, custom_montage_id: UUID) -> dict | None:
        """Never a DELETE. The clinical reasoning on a montage that was used to
        treat someone is part of the prescribing record - 38 says so in the
        table comment, and 32 revokes DELETE from anava_app anyway."""
        return await fetch_optional(
            self.session,
            text("UPDATE protocol_custom_montages SET is_active = FALSE, updated_at = NOW() WHERE custom_montage_id = :id RETURNING *"),
            {"id": str(custom_montage_id)},
        )


class ProtocolSessionRepository:
    """Protocol-born rows on the appointments spine.

    Every device_session row also sets clinic_device_id. 41 made that
    mandatory (chk_appointments_device_session_has_device is a biconditional:
    a device session must name the unit it runs on, everything else must not),
    and per-device capacity counts filter that column directly.

    Every row written here sets plan_id when the protocol has one. That is
    load-bearing, not cosmetic:
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


class ProtocolDeviceSessionRepository:
    """core.protocol_device_sessions (47) — one row per device session as the
    doctor set it up, pointing at the appointments row it generated.

    Never mirrors appointments' mutable state (status, start_time,
    hold_expires_at) — see 47's header for why that column set was left out
    on purpose.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("protocol_device_sessions", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_protocol(self, protocol_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM protocol_device_sessions WHERE protocol_id = :id ORDER BY session_number"),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


class ProtocolFollowupRepository:
    """core.protocol_followup (47) — one row per follow-up as the doctor set
    it up, pointing at the appointments row it generated. Same shape and same
    reasoning as ProtocolDeviceSessionRepository.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("protocol_followup", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_protocol(self, protocol_id: UUID) -> builtins.list[dict]:
        rows = (
            (
                await self.session.execute(
                    text("SELECT * FROM protocol_followup WHERE protocol_id = :id ORDER BY after_session_number"),
                    {"id": str(protocol_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


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
