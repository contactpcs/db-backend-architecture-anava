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
  * Session generation writes onto the appointments spine, always with
    plan_id set - see ProtocolSessionRepository for why that matters.
"""

from __future__ import annotations

import builtins
import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.core.scoping import assert_clinic_scope
from app.modules.treatment_protocols import schemas as s
from app.modules.treatment_protocols.repository import (
    CatalogueRepository,
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


def _placement_summary(row: dict) -> str | None:
    """One-line montage description, per device family."""
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

        sessions = [{"session_number": i + 1, "planned_date": d} for i, d in enumerate(dates)]

        follow_ups: builtins.list[dict] = []
        if req.follow_up_every_n:
            for item in sessions:
                if item["session_number"] % req.follow_up_every_n == 0:
                    follow_ups.append(
                        {
                            "after_session_number": item["session_number"],
                            # Day after the triggering session, so the doctor
                            # sees the patient once that block is complete.
                            "planned_date": item["planned_date"] + dt.timedelta(days=1),
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
        detail["sessions"] = [r for r in all_rows if r["appointment_type"] == _TYPE_DEVICE_SESSION]
        detail["follow_ups"] = [r for r in all_rows if r["appointment_type"] == _TYPE_FOLLOW_UP]
        return detail

    async def list(self, ctx: RequestContext, **filters) -> builtins.list[dict]:
        # Non-cross-clinic roles are pinned to their own clinic regardless of
        # what they asked for.
        if ctx.role not in ("super_admin", "regional_admin"):
            filters["clinic_id"] = UUID(ctx.clinic_id) if ctx.clinic_id else None
        return await self.repo.list(**filters)

    async def list_sessions(self, protocol_id: UUID, ctx: RequestContext, *, appointment_type: str | None = None) -> builtins.list[dict]:
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        return await self.sessions.list_for_protocol(protocol_id, appointment_type=appointment_type)

    # -- create ------------------------------------------------------------

    async def create(self, body: s.ProtocolCreate, ctx: RequestContext) -> dict:
        """Step 8. Creates the protocol and every appointment in one
        transaction, so a partially-booked course can never exist."""
        plan = await self.repo.plan_context(body.plan_id)
        if not plan:
            raise NotFoundError("Treatment plan not found", code="PLAN_NOT_FOUND")
        await assert_clinic_scope(ctx, self.session, plan["clinic_id"])

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

        # Map the caller's device-agnostic placement_id/dosing_id onto the
        # correct pair of the twelve nullable FK columns. The exactly-one
        # CHECKs then hold by construction.
        payload: dict[str, Any] = {
            "plan_id": str(body.plan_id),
            "device_id": str(body.device_id),
            "set_by": ctx.user_id,
            placement_pk(slug): str(body.placement_id),
            dosing_pk(slug): str(body.dosing_id),
            "session_count": body.session_count,
            "follow_up_every_n": body.follow_up_every_n,
            "status": "draft",
            "device_settings": body.device_settings or {},
            "notes": body.notes,
        }

        try:
            created = await self.repo.create(payload)
        except IntegrityError as exc:  # pragma: no cover - surfaced as 409
            raise ConflictError("Protocol violates a database constraint", code="PROTOCOL_CONSTRAINT") from exc

        protocol_id = created["protocol_id"]

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
        counts = await self._generate_appointments(protocol_id, plan, preview, ctx)

        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=protocol_id,
            event_type="treatment_protocol.created",
            payload={
                "protocol_id": str(protocol_id),
                "plan_id": str(body.plan_id),
                "device_id": str(body.device_id),
                "modality": device["modality"],
                "session_count": body.session_count,
                **counts,
            },
        )
        return await self.get_or_404(protocol_id)

    async def _generate_appointments(self, protocol_id: UUID, plan: dict, preview: dict, ctx: RequestContext) -> dict:
        """Writes the whole course onto the appointments spine.

        Every row is 'planned' with a date and no time - exactly the state
        31 describes ("doctor set a DATE at protocol setup. No slot yet, no
        hold"). The patient picks a time later, moving the row to 'selected'.
        A device session carries no doctor_id: 30 made that column nullable
        so a CA-run session does not occupy the supervising doctor's diary.
        """
        role = ctx.role or "doctor"
        for item in preview["sessions"]:
            await self.sessions.create(
                {
                    "clinic_id": str(plan["clinic_id"]),
                    "patient_id": str(plan["patient_id"]),
                    "doctor_id": None,
                    "plan_id": str(plan["plan_id"]),
                    "protocol_id": str(protocol_id),
                    "appointment_date": item["planned_date"],
                    "appointment_type": _TYPE_DEVICE_SESSION,
                    "session_number": item["session_number"],
                    "status": _STATUS_PLANNED,
                    "booked_by": ctx.user_id,
                    "booked_by_role": role,
                }
            )
        for item in preview["follow_ups"]:
            await self.sessions.create(
                {
                    "clinic_id": str(plan["clinic_id"]),
                    "patient_id": str(plan["patient_id"]),
                    # A follow-up IS a doctor consultation, so this one does
                    # carry the doctor.
                    "doctor_id": str(plan["doctor_id"]),
                    "plan_id": str(plan["plan_id"]),
                    "protocol_id": str(protocol_id),
                    "appointment_date": item["planned_date"],
                    "appointment_type": _TYPE_FOLLOW_UP,
                    "session_number": item["after_session_number"],
                    "status": _STATUS_PLANNED,
                    "booked_by": ctx.user_id,
                    "booked_by_role": role,
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
        updated = await self.repo.set_status(protocol_id, "active")
        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=protocol_id,
            event_type="treatment_protocol.activated",
            payload={"protocol_id": str(protocol_id), "plan_id": str(row["plan_id"])},
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
