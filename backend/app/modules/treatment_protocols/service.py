"""Business rules for the Treatment Protocol wizard.

The rulebook, in one place, because the DB can only carry part of it:

  * Electrode counts are a property of the DEVICE (tDCS = 1 anode + 1
    cathode, HD-tDCS = 1 anode + up to 4 returns). Enforced as a CHECK on
    each per-device placement table, and re-checked here so a custom montage
    is rejected before the doctor leaves step 4 rather than at INSERT time.
  * Placement and dosing must belong to the protocol's own device. That is
    a TRIGGER in the schema (a CHECK cannot read another table), and a
    pre-flight check here so the caller gets a 422 with a readable message
    instead of a raised PL/pgSQL exception.
  * Session generation writes onto the appointments spine. Device sessions
    carry clinic_device_id (41); follow-ups must not.
  * A protocol hangs off a protocol_instance (45), and only a protocol_
    instance (48) — instance_id is the sole parent. protocol_plan no longer
    references treatment_plans at all (48 dropped plan_id).
  * Each generated appointment gets a matching protocol_device_sessions /
    protocol_followup row (47) carrying its appointment_id — the doctor's
    setup record, kept separate from the appointment's own mutable state.
"""

from __future__ import annotations

import builtins
import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.core.scoping import assert_clinic_scope
from app.modules.treatment_protocols import schemas as s
from app.modules.treatment_protocols.repository import (
    CatalogueRepository,
    CustomMontageRepository,
    ProtocolDetailRepository,
    ProtocolDeviceSessionRepository,
    ProtocolFollowupRepository,
    ProtocolInstanceRepository,
    ProtocolPrsRepository,
    ProtocolRepository,
    ProtocolSessionRepository,
    dosing_pk,
    placement_pk,
)

# Which weekday indexes (0=Mon) a given sessions-per-week cadence lands on.
# Matches the wizard's own weekdaysFor() so a preview generated in the UI and
# one generated here agree.
_WEEKDAYS_FOR = {
    1: (2,),
    2: (1, 3),
    3: (0, 2, 4),
    4: (0, 1, 3, 4),
    5: (0, 1, 2, 3, 4),
    6: (0, 1, 2, 3, 4, 5),
    7: (0, 1, 2, 3, 4, 5, 6),
}

# Statuses a protocol may still be edited or cancelled from.
_MUTABLE_STATUSES = {"draft"}
_CANCELLABLE_STATUSES = {"draft", "active"}

# Appointment vocabulary this module writes. 30/31 both defer the
# appointment_type CHECK because the staff booking path still writes seven
# legacy values; when that CHECK lands these two literals must be in it.
_TYPE_DEVICE_SESSION = "device_session"
_TYPE_FOLLOW_UP = "protocol_followup"
_STATUS_PLANNED = "planned"

_STAFF_ROLES = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")


def _slug_for_modality(modality: str) -> str:
    slug = s.MODALITY_SLUG.get(modality)
    if slug is None:
        raise BusinessRuleError(f"Device modality '{modality}' has no catalogue tables", code="UNKNOWN_MODALITY")
    return slug


def _max_cathodes(modality: str) -> int:
    """HD-tDCS is a 4x1 ring: one anode, up to four returns. Everything else
    in phase 1 is a conventional two-electrode montage."""
    return 4 if modality == "HD-tDCS" else 1


def _placement_summary(row: dict | None) -> str | None:
    """One-line montage description, per device family.

    Accepts None because both callers pass the result of a lookup that may
    miss - a protocol whose device has no catalogued placement. The guard
    below already handled that; the annotation now says so.
    """
    if not row:
        return None
    modality = row.get("modality")
    if modality == "tDCS":
        a, c = row.get("anode_site"), row.get("cathode_site")
        return f"{a} -> {c}" if a and c else row.get("montage_label")
    if modality == "HD-tDCS":
        a, returns = row.get("anode_site"), row.get("return_sites") or []
        return f"{a} -> {', '.join(returns)}" if a and returns else row.get("montage_label")
    if modality == "taVNS":
        parts = [p for p in (row.get("ear_side"), row.get("auricular_site")) if p]
        return " / ".join(parts) or row.get("montage_label")
    if modality in ("TPS", "rTMS"):
        parts = [p for p in (row.get("coil_target") or row.get("target_region"), row.get("hemisphere")) if p]
        return " / ".join(parts) or row.get("montage_label")
    return row.get("montage_label")


class CatalogueService:
    """Steps 1-3 and the reference lookups for steps 4-6."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CatalogueRepository(session)

    async def list_companies(self, *, active_only: bool = True) -> builtins.list[dict]:
        return await self.repo.list_companies(active_only=active_only)

    async def list_devices(
        self, *, phase: int | None = None, active_only: bool = True, clinic_id: UUID | None = None
    ) -> builtins.list[dict]:
        return await self.repo.list_devices(phase=phase, active_only=active_only, clinic_id=clinic_id)

    async def get_device_or_404(self, device_id: UUID) -> dict:
        device = await self.repo.get_device(device_id)
        if not device:
            raise NotFoundError("Device not found", code="DEVICE_NOT_FOUND")
        return device

    async def list_conditions(self, *, device_id: UUID | None = None) -> builtins.list[dict]:
        return await self.repo.list_conditions(device_id=device_id)

    async def list_diagnoses(
        self,
        *,
        condition_ids: builtins.list[UUID] | None,
        query: str | None,
        device_id: UUID | None,
        skip: int,
        limit: int,
    ) -> builtins.list[dict]:
        rows = await self.repo.list_diagnoses(condition_ids=condition_ids, query=query, skip=skip, limit=limit)
        if not device_id or not rows:
            return rows
        # Hydrate the "Suggested" column: the best placement + evidence for
        # each row's condition on the chosen device.
        device = await self.get_device_or_404(device_id)
        slug = _slug_for_modality(device["modality"])
        cache: dict[str, tuple[str | None, str | None]] = {}
        for row in rows:
            cid = str(row["condition_id"])
            if cid not in cache:
                dosing = await self.repo.best_dosing_for_condition(slug, UUID(cid), device_id)
                summary = None
                if dosing and dosing.get("placement_id"):
                    placement = await self.repo.get_placement(slug, dosing["placement_id"])
                    summary = _placement_summary(placement) if placement else None
                cache[cid] = (summary, dosing["evidence_level"] if dosing else None)
            row["suggested_montage"], row["evidence_level"] = cache[cid]
        return rows

    async def resolve_diagnoses(self, *, diagnosis_ids: builtins.list[UUID], device_id: UUID) -> dict:
        """Step 3's ranked outcome.

        Highest evidence level among the selected codes wins. Alternates are
        returned so the doctor can override with one click - overriding is a
        clinical judgement the UI surfaces rather than hides.
        """
        device = await self.get_device_or_404(device_id)
        slug = _slug_for_modality(device["modality"])
        diagnoses = await self.repo.get_diagnoses_by_ids(diagnosis_ids)
        if not diagnoses:
            return {
                "alternates": [],
                "suggested_scales": [],
                "note": "Select at least one diagnosis code to see a suggested montage.",
            }

        # One candidate per distinct condition, ranked by evidence.
        seen: dict[str, dict] = {}
        for d in diagnoses:
            cid = str(d["condition_id"])
            if cid in seen:
                continue
            dosing = await self.repo.best_dosing_for_condition(slug, UUID(cid), device_id)
            placement = None
            if dosing and dosing.get("placement_id"):
                placement = await self.repo.get_placement(slug, dosing["placement_id"])
            seen[cid] = {
                "condition_id": d["condition_id"],
                "condition_name": d["condition_name"],
                "evidence_level": dosing["evidence_level"] if dosing else None,
                "placement_id": placement["placement_id"] if placement else None,
                "placement_summary": _placement_summary(placement),
                "dosing_id": dosing["dosing_id"] if dosing else None,
                "dosing": dosing,
            }

        ranked = sorted(
            seen.values(),
            key=lambda c: s.EVIDENCE_RANK.get(c["evidence_level"] or "", 0),
            reverse=True,
        )
        winner = ranked[0]
        alternates = ranked[1:]

        condition_ids = [UUID(str(c["condition_id"])) for c in ranked]
        scales = await self.repo.list_scales(condition_ids=condition_ids)

        note = None
        if winner["dosing_id"] is None:
            note = (
                f"No catalogued dosing for {winner['condition_name']} on {device['device_name']}. "
                "Placement and parameters must be set manually."
            )

        return {
            "driving_condition_id": winner["condition_id"],
            "driving_condition_name": winner["condition_name"],
            "evidence_level": winner["evidence_level"],
            "placement_id": winner["placement_id"],
            "placement_summary": winner["placement_summary"],
            "dosing_id": winner["dosing_id"],
            "suggested_dosing": winner["dosing"],
            "suggested_scales": [sc["scale_code"] for sc in scales],
            "alternates": [{k: v for k, v in a.items() if k != "dosing"} for a in alternates],
            "note": note,
        }

    async def list_placements(self, *, device_id: UUID, condition_id: UUID | None) -> builtins.list[dict]:
        device = await self.get_device_or_404(device_id)
        slug = _slug_for_modality(device["modality"])
        rows = await self.repo.list_placements(slug, condition_id=condition_id, device_id=device_id)
        for r in rows:
            r["summary"] = _placement_summary(r)
        return rows

    async def list_dosing(self, *, device_id: UUID, condition_id: UUID | None, placement_id: UUID | None) -> builtins.list[dict]:
        device = await self.get_device_or_404(device_id)
        slug = _slug_for_modality(device["modality"])
        return await self.repo.list_dosing(slug, condition_id=condition_id, device_id=device_id, placement_id=placement_id)

    async def list_scales(self, *, condition_ids: builtins.list[UUID] | None) -> builtins.list[dict]:
        return await self.repo.list_scales(condition_ids=condition_ids)

    async def validate_electrodes(self, body: s.ElectrodeValidationRequest) -> dict:
        """Step 4's guard rail.

        Same rule the per-device CHECK constraints enforce, applied early so
        the doctor sees a readable message on the step they are standing on.
        """
        device = await self.get_device_or_404(body.device_id)
        modality = device["modality"]
        max_cathodes = _max_cathodes(modality)
        errors: builtins.list[str] = []
        warnings: builtins.list[str] = []

        cathodes = [c for c in body.cathode_sites if c]
        if not body.anode_site:
            errors.append("No anode placed. Exactly one anode is required.")
        if not cathodes:
            errors.append("No cathode placed. A return electrode is required.")
        if len(cathodes) > max_cathodes:
            errors.append(f"{modality} allows at most {max_cathodes} cathode(s); {len(cathodes)} were placed.")
        if body.anode_site and body.anode_site in cathodes:
            errors.append(f"Site {body.anode_site} is assigned as both anode and cathode.")
        if len(cathodes) != len(set(cathodes)):
            errors.append("The same cathode site is used more than once.")
        if modality == "HD-tDCS" and cathodes and len(cathodes) < 4:
            warnings.append(f"HD-tDCS is normally a 4x1 ring; {len(cathodes)} return(s) placed.")

        return {
            "valid": not errors,
            "modality": modality,
            "max_cathodes": max_cathodes,
            "errors": errors,
            "warnings": warnings,
        }


class ScheduleService:
    """Step 7. Pure date arithmetic - touches no tables, so the doctor can
    re-preview as often as they like without writing anything."""

    @staticmethod
    def build(req: s.SchedulePreviewRequest) -> dict:
        weekdays = _WEEKDAYS_FOR[req.sessions_per_week]
        skip = {d for d in req.skip_dates}
        extra = sorted({d for d in req.extra_dates})

        # The cadence supplies session_count dates, minus any the doctor
        # painted in explicitly — an extra date is one of the requested
        # sessions moved onto an off-cadence day, not an extra visit on top.
        # Truncating after the merge instead would silently discard a
        # hand-picked date whenever the cadence had already filled the quota,
        # which is the opposite of what clicking that day means.
        extra_wanted = [d for d in extra if d not in skip]
        from_cadence = max(0, req.session_count - len(extra_wanted))

        dates: builtins.list[dt.date] = []
        cursor = req.start_date
        # Bounded walk: 90 sessions at 1/week is ~630 days, so 1000 is a
        # generous ceiling that still cannot spin.
        for _ in range(1000):
            if len(dates) >= from_cadence:
                break
            if cursor.weekday() in weekdays and cursor not in skip and cursor not in extra_wanted:
                dates.append(cursor)
            cursor += dt.timedelta(days=1)

        dates.extend(extra_wanted)
        dates = sorted(set(dates))[: req.session_count]

        # Built from the (number, date) pairs rather than read back out of the
        # dicts below: a dict mixing an int and a date widens to
        # dict[str, object], and the follow-up arithmetic then has no types to
        # work with.
        numbered = list(enumerate(dates, start=1))
        sessions = [{"session_number": n, "planned_date": d} for n, d in numbered]

        follow_ups: builtins.list[dict] = []
        if req.follow_up_every_n:
            for number, planned in numbered:
                if number % req.follow_up_every_n == 0:
                    follow_ups.append(
                        {
                            "after_session_number": number,
                            # Day after the triggering session, so the doctor
                            # sees the patient once that block is complete.
                            "planned_date": planned + dt.timedelta(days=1),
                        }
                    )

        first = dates[0] if dates else None
        last = dates[-1] if dates else None
        weeks = 0
        if first and last:
            weeks = ((last - first).days // 7) + 1

        return {
            "sessions": sessions,
            "follow_ups": follow_ups,
            "session_count": len(sessions),
            "follow_up_count": len(follow_ups),
            "first_date": first,
            "last_date": last,
            "week_count": weeks,
        }


class ProtocolService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ProtocolRepository(session)
        self.sessions = ProtocolSessionRepository(session)
        self.catalogue = CatalogueRepository(session)
        self.catalogue_svc = CatalogueService(session)
        self.details = ProtocolDetailRepository(session)
        self.instances = ProtocolInstanceRepository(session)
        self.device_sessions = ProtocolDeviceSessionRepository(session)
        self.followups = ProtocolFollowupRepository(session)

    # -- reads -------------------------------------------------------------

    async def get_or_404(self, protocol_id: UUID) -> dict:
        row = await self.repo.get(protocol_id)
        if not row:
            raise NotFoundError("Treatment protocol not found", code="PROTOCOL_NOT_FOUND")
        return row

    async def get_detail(self, protocol_id: UUID, ctx: RequestContext) -> dict:
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])

        slug = _slug_for_modality(row["modality"])
        placement_id = row.get(placement_pk(slug))
        dosing_id = row.get(dosing_pk(slug))

        placement = await self.catalogue.get_placement(slug, placement_id) if placement_id else None
        if placement:
            placement["summary"] = _placement_summary(placement)
        dosing = await self.catalogue.get_dosing(slug, dosing_id) if dosing_id else None

        all_rows = await self.sessions.list_for_protocol(protocol_id)
        detail = dict(row)
        detail["placement_id"] = placement_id
        detail["placement_summary"] = _placement_summary(placement)
        detail["dosing_id"] = dosing_id
        detail["placement"] = placement
        detail["dosing"] = dosing
        detail["conditions"] = await self.details.list_conditions(protocol_id)
        detail["diagnoses"] = await self.details.list_diagnoses(protocol_id)
        detail["scales"] = await self.details.list_scales(protocol_id)
        detail["sessions"] = [r for r in all_rows if r["appointment_type"] == _TYPE_DEVICE_SESSION]
        detail["follow_ups"] = [r for r in all_rows if r["appointment_type"] == _TYPE_FOLLOW_UP]
        return detail

    async def list(self, ctx: RequestContext, **filters) -> builtins.list[dict]:
        # Non-cross-clinic roles are pinned to their own clinic regardless of
        # what they asked for.
        if ctx.role not in ("super_admin", "regional_admin"):
            filters["clinic_id"] = UUID(ctx.clinic_id) if ctx.clinic_id else None
        # The API takes patients.patient_id; every patient_id-shaped FK in the
        # schema stores profiles.id (NOTES.md). Filtering on the raw API id
        # matched nothing and returned an empty list with no error — the
        # Treatment Protocol tab showed "no protocol yet" for a patient who
        # had one.
        if filters.get("patient_id"):
            filters["patient_id"] = await _resolve_patient_profile_id(self.session, filters["patient_id"])
        return await self.repo.list(**filters)

    async def list_sessions(self, protocol_id: UUID, ctx: RequestContext, *, appointment_type: str | None = None) -> builtins.list[dict]:
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        return await self.sessions.list_for_protocol(protocol_id, appointment_type=appointment_type)

    # -- create ------------------------------------------------------------

    async def create(self, body: s.ProtocolCreate, ctx: RequestContext) -> dict:
        """Step 8. Creates the protocol and every appointment in one
        transaction, so a partially-booked course can never exist."""
        # 45 re-parented the protocol onto protocol_instances; 48 made that
        # the only parent. Resolve it into patient/doctor/clinic once.
        parent = await self._resolve_parent(body)
        await assert_clinic_scope(ctx, self.session, parent["clinic_id"])

        device = await self.catalogue_svc.get_device_or_404(body.device_id)
        if not device["is_active"]:
            raise BusinessRuleError("Device is not active", code="DEVICE_INACTIVE")
        if device["phase"] != 1:
            raise BusinessRuleError(
                f"{device['device_name']} is catalogued but not yet enabled for prescribing (phase {device['phase']}).",
                code="DEVICE_NOT_ENABLED",
            )

        slug = _slug_for_modality(device["modality"])

        # Same-device consistency. The schema enforces this with a trigger
        # because a CHECK cannot read another table; checking here first
        # turns a raw PL/pgSQL exception into a readable 422.
        placement = await self.catalogue.get_placement(slug, body.placement_id)
        if not placement:
            raise NotFoundError("Placement not found for this device family", code="PLACEMENT_NOT_FOUND")
        if str(placement["device_id"]) != str(body.device_id):
            raise ValidationError("Placement belongs to a different device", code="PLACEMENT_DEVICE_MISMATCH")

        dosing = await self.catalogue.get_dosing(slug, body.dosing_id)
        if not dosing:
            raise NotFoundError("Dosing not found for this device family", code="DOSING_NOT_FOUND")
        if str(dosing["device_id"]) != str(body.device_id):
            raise ValidationError("Dosing belongs to a different device", code="DOSING_DEVICE_MISMATCH")

        # The clinic must actually own the machine. trg_check_device_available
        # _at_clinic (37) enforces this, but a readable 422 beats a raised
        # PL/pgSQL exception, and the doctor needs to know it is an inventory
        # problem rather than a prescribing one.
        if not await self.repo.clinic_has_device(parent["clinic_id"], body.device_id):
            raise BusinessRuleError(
                f"{device['device_name']} is not available at this clinic.",
                code="DEVICE_NOT_AT_CLINIC",
            )

        # Validate the child-table references BEFORE the protocol row is
        # written. A bad diagnosis_id caught here is a 404; caught after the
        # INSERT it is a rolled-back transaction with a foreign-key message
        # that names a constraint rather than the field the caller got wrong.
        await self._assert_conditions_exist(body.conditions)
        await self._assert_diagnoses_exist(body.diagnosis_ids)
        await self._assert_scales_exist(body.scales)

        if body.authored_in_appointment_id is not None:
            appt = await self.sessions.get_appointment(body.authored_in_appointment_id)
            if not appt:
                raise NotFoundError("Authoring appointment not found", code="APPOINTMENT_NOT_FOUND")
            if str(appt["patient_id"]) != str(parent["patient_id"]):
                raise ValidationError(
                    "Authoring appointment belongs to a different patient",
                    code="APPOINTMENT_PATIENT_MISMATCH",
                )

        # Map the caller's device-agnostic placement_id/dosing_id onto the
        # correct pair of the twelve nullable FK columns. The exactly-one
        # CHECKs then hold by construction.
        payload: dict[str, Any] = {
            "instance_id": str(body.instance_id),
            "device_id": str(body.device_id),
            "set_by": ctx.user_id,
            placement_pk(slug): str(body.placement_id),
            dosing_pk(slug): str(body.dosing_id),
            "session_count": body.session_count,
            "follow_up_every_n": body.follow_up_every_n,
            "status": "draft",
            "device_settings": body.device_settings or {},
            "notes": body.notes,
            # 39. sessions_per_week is persisted rather than consumed once by
            # the generator and discarded - without it the calendar cannot be
            # regenerated or audited against what was prescribed.
            "authored_in_appointment_id": (str(body.authored_in_appointment_id) if body.authored_in_appointment_id else None),
            "prescribed_current_ma": body.prescribed_current_ma,
            "prescribed_duration_min": body.prescribed_duration_min,
            "ramp_seconds": body.ramp_seconds,
            "sessions_per_week": body.sessions_per_week,
        }

        try:
            created = await self.repo.create(payload)
        except IntegrityError as exc:  # pragma: no cover - surfaced as 409
            raise ConflictError("Protocol violates a database constraint", code="PROTOCOL_CONSTRAINT") from exc

        protocol_id = created["protocol_id"]

        # Steps 2, 3 and 6. Same transaction as the protocol row, so a
        # prescription cannot exist without the diagnosis that justifies it.
        conditions_written = await self.details.add_conditions(protocol_id, [c.model_dump() for c in body.conditions])
        diagnoses_written = await self.details.add_diagnoses(protocol_id, body.diagnosis_ids)
        scales_written = await self.details.add_scales(protocol_id, [sc.model_dump() for sc in body.scales])

        preview = ScheduleService.build(
            s.SchedulePreviewRequest(
                start_date=body.start_date,
                session_count=body.session_count,
                sessions_per_week=body.sessions_per_week,
                follow_up_every_n=body.follow_up_every_n,
                skip_dates=body.skip_dates,
                extra_dates=body.extra_dates,
            )
        )
        counts = await self._generate_appointments(protocol_id, parent, preview, ctx, device_id=body.device_id)

        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=protocol_id,
            event_type="treatment_protocol.created",
            payload={
                "protocol_id": str(protocol_id),
                "instance_id": str(body.instance_id),
                "device_id": str(body.device_id),
                "modality": device["modality"],
                "session_count": body.session_count,
                "sessions_per_week": body.sessions_per_week,
                "conditions_recorded": conditions_written,
                "diagnoses_recorded": diagnoses_written,
                "scales_assigned": scales_written,
                **counts,
            },
        )
        return await self.get_or_404(protocol_id)

    # -- pre-flight validation for the child tables ------------------------

    async def _assert_conditions_exist(self, items: builtins.list[s.ProtocolConditionAssignment]) -> None:
        ids = [c.condition_id for c in items if c.condition_id is not None]
        if not ids:
            return
        found = await self.catalogue.get_conditions_by_ids(ids)
        missing = {str(i) for i in ids} - {str(r["condition_id"]) for r in found}
        if missing:
            raise NotFoundError(f"Unknown condition_id: {', '.join(sorted(missing))}", code="CONDITION_NOT_FOUND")

    async def _assert_diagnoses_exist(self, diagnosis_ids: builtins.list[UUID]) -> None:
        if not diagnosis_ids:
            return
        found = await self.catalogue.get_diagnoses_by_ids(diagnosis_ids)
        missing = {str(i) for i in diagnosis_ids} - {str(r["diagnosis_id"]) for r in found}
        if missing:
            raise NotFoundError(f"Unknown diagnosis_id: {', '.join(sorted(missing))}", code="DIAGNOSIS_NOT_FOUND")

    async def _assert_scales_exist(self, items: builtins.list[s.ProtocolScaleAssignment]) -> None:
        ids = [sc.scale_id for sc in items]
        if not ids:
            return
        found = await self.catalogue.get_scales_by_ids(ids)
        missing = {str(i) for i in ids} - {str(r["scale_id"]) for r in found}
        if missing:
            raise NotFoundError(f"Unknown scale_id: {', '.join(sorted(missing))}", code="SCALE_NOT_FOUND")

    async def _resolve_parent(self, body: s.ProtocolCreate) -> dict:
        """Patient, doctor, clinic from the protocol's one parent — the
        instance. 48 dropped plan_id entirely; protocol_plan no longer
        references treatment_plans in any form.
        """
        instance = await self.repo.instance_context(body.instance_id)
        if not instance:
            raise NotFoundError("Protocol instance not found", code="INSTANCE_NOT_FOUND")
        if instance["instance_status"] in ("cancelled", "completed", "superseded"):
            raise BusinessRuleError(
                f"Protocol instance is '{instance['instance_status']}' and cannot take a new prescription",
                code="INSTANCE_NOT_OPEN",
            )
        return {
            "instance_id": body.instance_id,
            "patient_id": instance["patient_id"],
            "doctor_id": instance["doctor_id"],
            "clinic_id": instance["clinic_id"],
        }

    async def _generate_appointments(self, protocol_id: UUID, parent: dict, preview: dict, ctx: RequestContext, *, device_id: UUID) -> dict:
        """Writes the whole course onto the appointments spine, and — 47 — a
        matching protocol_device_sessions / protocol_followup row alongside
        each one, carrying the appointment_id it just got back.

        Every appointments row is 'planned' with a date and no time - exactly
        the state 31 describes ("doctor set a DATE at protocol setup. No slot
        yet, no hold"). The patient picks a time later, moving the row to
        'selected'. A device session carries no doctor_id: 30 made that
        column nullable so a CA-run session does not occupy the supervising
        doctor's diary.

        Device sessions also carry clinic_device_id, which 41 made mandatory:
        chk_appointments_device_session_has_device is a biconditional, so a
        device session MUST name the unit it runs on and a follow-up must NOT.
        Resolved once here rather than per row, the same way 41's own
        fn_generate_protocol_sessions resolves it.

        The 47 tables are written appointment-by-appointment, in the same
        transaction, rather than batched after: appointment_id is NOT NULL on
        both, so there is no half-written intermediate state to leave behind
        if a later row in the loop fails — the whole create() transaction
        rolls back together, same as it already does for the appointments
        themselves.
        """
        role = ctx.role or "doctor"
        instance_id = parent["instance_id"]
        clinic_id = parent["clinic_id"]

        clinic_device_id = await self.repo.clinic_device_for(clinic_id, device_id)
        if clinic_device_id is None:
            # trg_check_device_available_at_clinic (37) would refuse the
            # protocol too, but this fires before any appointment is written
            # and names the actual problem instead of a CHECK violation.
            raise BusinessRuleError(
                "The clinic has no active unit of this device to book sessions against.",
                code="NO_CLINIC_DEVICE",
            )

        for item in preview["sessions"]:
            appt = await self.sessions.create(
                {
                    "clinic_id": str(clinic_id),
                    "patient_id": str(parent["patient_id"]),
                    "doctor_id": None,
                    "protocol_id": str(protocol_id),
                    "clinic_device_id": str(clinic_device_id),
                    "appointment_date": item["planned_date"],
                    "appointment_type": _TYPE_DEVICE_SESSION,
                    "session_number": item["session_number"],
                    "status": _STATUS_PLANNED,
                    "booked_by": ctx.user_id,
                    "booked_by_role": role,
                }
            )
            await self.device_sessions.create(
                {
                    "instance_id": str(instance_id),
                    "protocol_id": str(protocol_id),
                    "appointment_id": str(appt["appointment_id"]),
                    "clinic_device_id": str(clinic_device_id),
                    "session_number": item["session_number"],
                    "planned_date": item["planned_date"],
                }
            )
        for item in preview["follow_ups"]:
            appt = await self.sessions.create(
                {
                    "clinic_id": str(clinic_id),
                    "patient_id": str(parent["patient_id"]),
                    # A follow-up IS a doctor consultation, so this one does
                    # carry the doctor - and no device: the same CHECK that
                    # requires clinic_device_id on a session forbids it here.
                    "doctor_id": str(parent["doctor_id"]) if parent.get("doctor_id") else None,
                    "protocol_id": str(protocol_id),
                    "appointment_date": item["planned_date"],
                    "appointment_type": _TYPE_FOLLOW_UP,
                    "session_number": item["after_session_number"],
                    "status": _STATUS_PLANNED,
                    "booked_by": ctx.user_id,
                    "booked_by_role": role,
                }
            )
            await self.followups.create(
                {
                    "instance_id": str(instance_id),
                    "protocol_id": str(protocol_id),
                    "appointment_id": str(appt["appointment_id"]),
                    "after_session_number": item["after_session_number"],
                    "planned_date": item["planned_date"],
                }
            )
        return {
            "sessions_created": len(preview["sessions"]),
            "follow_ups_created": len(preview["follow_ups"]),
        }

    # -- lifecycle ---------------------------------------------------------

    async def update(self, protocol_id: UUID, body: s.ProtocolUpdate, ctx: RequestContext) -> dict:
        """Draft-only.

        Once a protocol is active its appointments exist, the patient has
        been notified and scales are queued. Changing session_count then
        would silently desynchronise the calendar from the protocol, so an
        active protocol is amended by cancelling and re-issuing rather than
        edited in place.
        """
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        if row["status"] not in _MUTABLE_STATUSES:
            raise BusinessRuleError(
                f"A protocol in status '{row['status']}' cannot be edited. Cancel it and issue an amendment.",
                code="PROTOCOL_NOT_EDITABLE",
            )
        fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        if not fields:
            return row
        if "session_count" in fields:
            follow_up = fields.get("follow_up_every_n", row["follow_up_every_n"])
            if follow_up and follow_up > fields["session_count"]:
                raise ValidationError("follow_up_every_n cannot exceed session_count", code="FOLLOW_UP_TOO_LARGE")
        updated = await self.repo.update(protocol_id, fields)
        return updated or row

    async def activate(self, protocol_id: UUID, ctx: RequestContext) -> dict:
        """The 'Push' action from step 8."""
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        if row["status"] == "active":
            raise ConflictError("Protocol is already active", code="PROTOCOL_ALREADY_ACTIVE")
        if row["status"] != "draft":
            raise BusinessRuleError(
                f"Only a draft protocol can be activated (current status: '{row['status']}')",
                code="PROTOCOL_NOT_DRAFT",
            )
        if not await self.repo.has_generated_appointments(protocol_id):
            raise BusinessRuleError(
                "Protocol has no generated sessions. Regenerate the schedule before activating.",
                code="PROTOCOL_NO_SESSIONS",
            )

        # fn_check_protocol_prescription_complete (39) refuses this transition
        # if the dose is incomplete. Checking here first names the missing
        # fields as a 422 the UI can map back onto step 5, instead of a raised
        # PL/pgSQL exception surfacing as a 500.
        missing = [field for field in ("prescribed_current_ma", "prescribed_duration_min", "sessions_per_week") if row.get(field) is None]
        if missing:
            raise BusinessRuleError(
                "Prescription is incomplete and cannot be activated. Missing: " + ", ".join(missing),
                code="PRESCRIPTION_INCOMPLETE",
            )

        updated = await self.repo.set_status(protocol_id, "active")
        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=protocol_id,
            event_type="treatment_protocol.activated",
            payload={"protocol_id": str(protocol_id), "instance_id": str(row["instance_id"])},
        )
        return updated or row

    async def cancel(self, protocol_id: UUID, ctx: RequestContext, *, reason: str) -> dict:
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        if row["status"] not in _CANCELLABLE_STATUSES:
            raise BusinessRuleError(
                f"A protocol in status '{row['status']}' cannot be cancelled",
                code="PROTOCOL_NOT_CANCELLABLE",
            )
        cancelled = await self.sessions.cancel_planned(protocol_id, reason=reason)
        updated = await self.repo.set_status(protocol_id, "cancelled")
        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=protocol_id,
            event_type="treatment_protocol.cancelled",
            payload={"protocol_id": str(protocol_id), "reason": reason, "sessions_cancelled": cancelled},
        )
        return updated or row

    async def complete(self, protocol_id: UUID, ctx: RequestContext) -> dict:
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        if row["status"] != "active":
            raise BusinessRuleError(
                f"Only an active protocol can be completed (current status: '{row['status']}')",
                code="PROTOCOL_NOT_ACTIVE",
            )
        updated = await self.repo.set_status(protocol_id, "completed")
        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=protocol_id,
            event_type="treatment_protocol.completed",
            payload={"protocol_id": str(protocol_id)},
        )
        return updated or row


class ProtocolInstanceService:
    """core.protocol_instances (45) — the course of device treatment.

    Sits between the cycle and the prescription. The Treatment Protocol tab
    lists instances; each instance holds the protocols prescribed within it,
    including amendments (a new protocol superseding an earlier one).
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ProtocolInstanceRepository(session)
        self.protocols = ProtocolRepository(session)

    async def get_or_404(self, instance_id: UUID) -> dict:
        row = await self.repo.get(instance_id)
        if not row:
            raise NotFoundError("Protocol instance not found", code="INSTANCE_NOT_FOUND")
        return row

    async def create(self, body: s.ProtocolInstanceCreate, ctx: RequestContext) -> dict:
        cycle = await self.protocols.cycle_context(body.cycle_id)
        if not cycle:
            raise NotFoundError("Treatment cycle not found", code="CYCLE_NOT_FOUND")
        await assert_clinic_scope(ctx, self.session, cycle["clinic_id"])

        if cycle["status"] != "in_progress":
            raise BusinessRuleError(
                f"Treatment cycle is '{cycle['status']}' — a protocol course can only be opened on an active cycle",
                code="CYCLE_NOT_ACTIVE",
            )

        # uq_protocol_instances_one_active enforces this too; checking first
        # turns a 23505 into a message naming the instance already open, which
        # is what the caller should reuse rather than duplicate.
        existing = await self.repo.get_open_for_cycle(body.cycle_id)
        if existing:
            raise ConflictError(
                f"This cycle already has an open protocol course (instance {existing['instance_number']}). "
                "Complete or cancel it before opening another.",
                code="INSTANCE_ALREADY_OPEN",
            )

        number = await self.repo.next_instance_number(body.cycle_id)
        try:
            created = await self.repo.create(
                {
                    "cycle_id": str(body.cycle_id),
                    "patient_id": str(cycle["patient_id"]),
                    "created_by": ctx.user_id,
                    "instance_number": number,
                    "status": "draft",
                    "notes": body.notes,
                }
            )
        except IntegrityError as exc:
            raise ConflictError("A protocol course already exists for this cycle", code="INSTANCE_ALREADY_OPEN") from exc

        await emit_event(
            self.session,
            aggregate_type="protocol_instance",
            aggregate_id=created["instance_id"],
            event_type="protocol_instance.created",
            payload={
                "instance_id": str(created["instance_id"]),
                "cycle_id": str(body.cycle_id),
                "instance_number": number,
            },
        )
        return await self.get_or_404(created["instance_id"])

    async def list(self, ctx: RequestContext, **filters) -> builtins.list[dict]:
        if ctx.role not in ("super_admin", "regional_admin"):
            filters["clinic_id"] = UUID(ctx.clinic_id) if ctx.clinic_id else None
        # protocol_instances.patient_id is profiles.id — same resolution the
        # protocol list needs, for the same reason.
        if filters.get("patient_id"):
            filters["patient_id"] = await _resolve_patient_profile_id(self.session, filters["patient_id"])
        return await self.repo.list(**filters)

    async def set_status(self, instance_id: UUID, status: str, ctx: RequestContext) -> dict:
        row = await self.get_or_404(instance_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        if status not in ("active", "completed", "cancelled", "superseded"):
            raise ValidationError(f"Unknown instance status '{status}'", code="INVALID_STATUS")
        if row["status"] == status:
            return row
        if row["status"] in ("completed", "cancelled") and status != "superseded":
            raise BusinessRuleError(
                f"A '{row['status']}' instance cannot move to '{status}'",
                code="INSTANCE_NOT_MUTABLE",
            )
        updated = await self.repo.set_status(instance_id, status)
        await emit_event(
            self.session,
            aggregate_type="protocol_instance",
            aggregate_id=instance_id,
            event_type=f"protocol_instance.{status}",
            payload={"instance_id": str(instance_id), "status": status},
        )
        return await self.get_or_404(instance_id) if updated else row


class CustomMontageService:
    """Step 4's "Custom Montage" panel.

    Writes core.protocol_custom_montages, never reference.*_placements: 32
    revokes application writes on the curated library precisely so a montage
    one doctor invented cannot end up looking like one the catalogue
    validated.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CustomMontageRepository(session)
        self.catalogue_svc = CatalogueService(session)

    async def create(self, body: s.CustomMontageCreate, ctx: RequestContext) -> dict:
        device = await self.catalogue_svc.get_device_or_404(body.device_id)

        # Same rule the placement validator applies, re-applied at the write
        # so a direct API call cannot bypass step 4's guard rail. The table's
        # own chk_pcm_electrode_shape enforces the shape both modalities
        # share (1 anode, 1-4 cathodes); this adds the per-modality half a
        # CHECK cannot express without knowing the device.
        result = await self.catalogue_svc.validate_electrodes(
            s.ElectrodeValidationRequest(
                device_id=body.device_id,
                anode_site=body.anode_sites[0] if body.anode_sites else None,
                cathode_sites=body.cathode_sites,
            )
        )
        if not result["valid"]:
            raise ValidationError("; ".join(result["errors"]), code="INVALID_MONTAGE")

        if body.condition_id is not None:
            if not await self.catalogue_svc.repo.get_condition(body.condition_id):
                raise NotFoundError("Condition not found", code="CONDITION_NOT_FOUND")

        try:
            created = await self.repo.create(
                {
                    "created_by": ctx.user_id,
                    "clinic_id": ctx.clinic_id,
                    "device_id": str(body.device_id),
                    "condition_id": str(body.condition_id) if body.condition_id else None,
                    "montage_name": body.montage_name,
                    "anode_sites": body.anode_sites,
                    "cathode_sites": body.cathode_sites,
                    "description": body.description,
                    "clinical_reasoning": body.clinical_reasoning,
                }
            )
        except IntegrityError as exc:
            # protocol_custom_montages_created_by_montage_name_key: the picker
            # shows names, so two of the same name are indistinguishable.
            raise ConflictError(
                f"You already have a montage named '{body.montage_name}'",
                code="MONTAGE_NAME_TAKEN",
            ) from exc

        await emit_event(
            self.session,
            aggregate_type="protocol_custom_montage",
            aggregate_id=created["custom_montage_id"],
            event_type="protocol_custom_montage.created",
            payload={
                "custom_montage_id": str(created["custom_montage_id"]),
                "device_id": str(body.device_id),
                "modality": device["modality"],
                "created_by": ctx.user_id,
            },
        )
        return await self.repo.get(created["custom_montage_id"]) or created

    async def list(self, ctx: RequestContext, **filters) -> builtins.list[dict]:
        # A doctor sees their own montages plus anything unscoped at their
        # clinic. Cross-clinic roles see everything.
        if ctx.role not in ("super_admin", "regional_admin") and ctx.clinic_id:
            filters.setdefault("clinic_id", UUID(ctx.clinic_id))
        return await self.repo.list(**filters)

    async def get_or_404(self, custom_montage_id: UUID) -> dict:
        row = await self.repo.get(custom_montage_id)
        if not row:
            raise NotFoundError("Custom montage not found", code="MONTAGE_NOT_FOUND")
        return row

    async def deactivate(self, custom_montage_id: UUID, ctx: RequestContext) -> dict:
        row = await self.get_or_404(custom_montage_id)
        # Only the author, or an admin, retires a montage. Another doctor's
        # clinical reasoning is not theirs to withdraw.
        if str(row["created_by"]) != str(ctx.user_id) and ctx.role not in ("super_admin", "regional_admin", "clinic_admin"):
            raise BusinessRuleError(
                "Only the author or an administrator can retire a custom montage",
                code="MONTAGE_NOT_OWNED",
            )
        updated = await self.repo.deactivate(custom_montage_id)
        return await self.repo.get(custom_montage_id) or updated or row


class ProtocolPrsService:
    """PRS responses against a protocol.

    Which table a response lands in is decided by the appointment's type,
    never by the caller - the schema's own triggers enforce the same rule,
    and resolving it server-side means a client cannot file a follow-up
    score as a device-session score.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ProtocolPrsRepository(session)
        self.sessions = ProtocolSessionRepository(session)
        self.protocols = ProtocolRepository(session)

    async def _appointment_for_protocol(self, protocol_id: UUID, appointment_id: UUID) -> dict:
        appt = await self.sessions.get_appointment(appointment_id)
        if not appt:
            raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
        if str(appt.get("protocol_id") or "") != str(protocol_id):
            raise ValidationError("Appointment does not belong to this protocol", code="APPOINTMENT_PROTOCOL_MISMATCH")
        return appt

    async def record_device_session(self, protocol_id: UUID, body: s.DeviceSessionPrsCreate, ctx: RequestContext) -> dict:
        protocol = await self.protocols.get(protocol_id)
        if not protocol:
            raise NotFoundError("Treatment protocol not found", code="PROTOCOL_NOT_FOUND")
        await assert_clinic_scope(ctx, self.session, protocol["clinic_id"])
        appt = await self._appointment_for_protocol(protocol_id, body.appointment_id)
        if appt["appointment_type"] != _TYPE_DEVICE_SESSION:
            raise ValidationError(
                f"Appointment is a '{appt['appointment_type']}', not a device session",
                code="WRONG_APPOINTMENT_TYPE",
            )
        try:
            row = await self.repo.create_device_session(
                {
                    "appointment_id": str(body.appointment_id),
                    "protocol_id": str(protocol_id),
                    "session_number": body.session_number,
                    "instance_id": body.instance_id,
                    "patient_id": str(appt["patient_id"]),
                }
            )
        except IntegrityError as exc:
            # uq_ds_prs_appointment: one PRS per visit.
            raise ConflictError("A PRS response already exists for this session", code="PRS_ALREADY_RECORDED") from exc
        return {**row, "response_id": row["ds_prs_id"], "kind": "device_session"}

    async def record_follow_up(self, protocol_id: UUID, body: s.FollowUpPrsCreate, ctx: RequestContext) -> dict:
        protocol = await self.protocols.get(protocol_id)
        if not protocol:
            raise NotFoundError("Treatment protocol not found", code="PROTOCOL_NOT_FOUND")
        await assert_clinic_scope(ctx, self.session, protocol["clinic_id"])
        appt = await self._appointment_for_protocol(protocol_id, body.appointment_id)
        if appt["appointment_type"] != _TYPE_FOLLOW_UP:
            raise ValidationError(
                f"Appointment is a '{appt['appointment_type']}', not a protocol follow-up",
                code="WRONG_APPOINTMENT_TYPE",
            )
        try:
            row = await self.repo.create_follow_up(
                {
                    "appointment_id": str(body.appointment_id),
                    "protocol_id": str(protocol_id),
                    "after_session_number": body.after_session_number,
                    "instance_id": body.instance_id,
                    "patient_id": str(appt["patient_id"]),
                }
            )
        except IntegrityError as exc:
            raise ConflictError("A PRS response already exists for this follow-up", code="PRS_ALREADY_RECORDED") from exc
        return {**row, "response_id": row["fu_prs_id"], "session_number": row["after_session_number"], "kind": "follow_up"}

    async def list_for_protocol(self, protocol_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        protocol = await self.protocols.get(protocol_id)
        if not protocol:
            raise NotFoundError("Treatment protocol not found", code="PROTOCOL_NOT_FOUND")
        await assert_clinic_scope(ctx, self.session, protocol["clinic_id"])
        return await self.repo.list_for_protocol(protocol_id)
