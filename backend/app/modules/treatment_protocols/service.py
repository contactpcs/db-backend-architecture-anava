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

from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionError_, ValidationError
from app.core.resolve import resolve_doctor_profile_id as _resolve_doctor_profile_id
from app.core.resolve import resolve_patient_profile_id as _resolve_patient_profile_id
from app.core.scoping import assert_clinic_scope, assert_owns_profile
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

# What a first protocol waits on (ProtocolService.readiness), as shown to the doctor.
_PREREQUISITE_LABELS = {
    "initial_consultation": "start the initial consultation",
    "anamnesis": "answer all required anamnesis questions",
    "prs": "complete a PRS",
    "doctor_notes": "add a doctor's note",
}


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
    if modality == "tVNS":
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

    # -- catalogue writes (super_admin) --------------------------------------
    # No delete: both tables are Retention Bucket 3 — retire with is_active=false.

    async def _get_company_or_404(self, company_id: UUID) -> dict:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundError("Device company not found", code="DEVICE_COMPANY_NOT_FOUND")
        return company

    async def create_company(self, fields: dict, *, ctx: RequestContext) -> dict:
        try:
            company = await self.repo.create_company(fields)
        except IntegrityError as exc:
            raise ConflictError("A company with this code or name already exists", code="DEVICE_COMPANY_CONFLICT") from exc
        await self._audit("device_company", company["company_id"], "device_company_created", fields, ctx)
        return company

    async def update_company(self, company_id: UUID, fields: dict, *, ctx: RequestContext) -> dict:
        existing = await self._get_company_or_404(company_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            return existing
        try:
            company = await self.repo.update_company(company_id, clean)
        except IntegrityError as exc:
            raise ConflictError("A company with this name already exists", code="DEVICE_COMPANY_CONFLICT") from exc
        await self._audit("device_company", company_id, "device_company_updated", clean, ctx)
        return company  # type: ignore[return-value]

    async def create_device(self, fields: dict, *, ctx: RequestContext) -> dict:
        company = await self._get_company_or_404(fields["company_id"])
        if not company["is_active"]:
            raise BusinessRuleError("Cannot add a device under an inactive company", code="DEVICE_COMPANY_INACTIVE")
        clean = {k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items()}
        try:
            row = await self.repo.create_device(clean)
        except IntegrityError as exc:
            raise ConflictError("A device with this code already exists", code="DEVICE_CONFLICT") from exc
        await self._audit("neuromod_device", row["device_id"], "device_created", clean, ctx)
        return await self.get_device_or_404(row["device_id"])

    async def update_device(self, device_id: UUID, fields: dict, *, ctx: RequestContext) -> dict:
        existing = await self.get_device_or_404(device_id)
        clean = {k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items() if v is not None}
        if not clean:
            return existing
        if "company_id" in clean:
            await self._get_company_or_404(UUID(clean["company_id"]))
        await self.repo.update_device(device_id, clean)
        await self._audit("neuromod_device", device_id, "device_updated", clean, ctx)
        return await self.get_device_or_404(device_id)

    async def _audit(self, aggregate_type: str, aggregate_id: Any, event_type: str, changes: dict, ctx: RequestContext) -> None:
        # Who changed the catalogue, and what — the hand-run-SQL era had none of this.
        await emit_event(
            self.session,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=event_type,
            payload={"changed_by": ctx.user_id, "changes": {k: str(v) if isinstance(v, UUID) else v for k, v in changes.items()}},
        )

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
    def follow_up_date(after: dt.date, weekdays, skip: set[dt.date] | frozenset[dt.date] = frozenset()) -> dt.date:
        """The day after the triggering session, walked forward onto the next
        day this cadence actually treats on and that is not in `skip`. The skip
        set carries the days the clinic is closed (the wizard sends them with the
        doctor's own skipped days), so a follow-up is never placed on a closed day.
        The walk is bounded: 60 days is far past any run of closed days."""
        day = after + dt.timedelta(days=1)
        for _ in range(60):
            if day.weekday() in weekdays and day not in skip:
                break
            day += dt.timedelta(days=1)
        return day

    @staticmethod
    def build(req: s.SchedulePreviewRequest) -> dict:
        weekdays = _WEEKDAYS_FOR[req.sessions_per_week]
        per_day = req.sessions_per_day or 1
        skip = {d for d in req.skip_dates}
        extra = sorted({d for d in req.extra_dates})

        # Treatment DAYS first, then sessions: each day holds per_day
        # sessions, so the course needs ceil(session_count / per_day) days
        # (the last one may hold fewer).
        days_needed = -(-req.session_count // per_day)

        # The cadence supplies those days, minus any the doctor painted in
        # explicitly — an extra date is one of the requested treatment days
        # moved onto an off-cadence day, not an extra visit on top.
        # Truncating after the merge instead would silently discard a
        # hand-picked date whenever the cadence had already filled the quota,
        # which is the opposite of what clicking that day means.
        extra_wanted = [d for d in extra if d not in skip]
        from_cadence = max(0, days_needed - len(extra_wanted))

        days: builtins.list[dt.date] = []
        cursor = req.start_date
        # Bounded walk: 90 sessions at 1/week is ~630 days, so 1000 is a
        # generous ceiling that still cannot spin.
        for _ in range(1000):
            if len(days) >= from_cadence:
                break
            if cursor.weekday() in weekdays and cursor not in skip and cursor not in extra_wanted:
                days.append(cursor)
            cursor += dt.timedelta(days=1)

        days.extend(extra_wanted)
        days = sorted(set(days))[:days_needed]
        dates = [d for d in days for _ in range(per_day)][: req.session_count]

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
                    # Day after the triggering session, so the doctor sees the
                    # patient once that block is complete — but walked forward
                    # onto the next day this cadence actually treats on. A
                    # blind +1 day landed on whatever day followed, including
                    # a day the clinic doesn't run this cadence on at all
                    # (e.g. Saturday for a Mon/Wed/Fri 3x/week protocol).
                    follow_ups.append(
                        {
                            "after_session_number": number,
                            "planned_date": ScheduleService.follow_up_date(planned, weekdays, skip),
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
            "day_count": len(set(dates)),
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
        self.custom_montages = CustomMontageRepository(session)

    # -- reads -------------------------------------------------------------

    async def get_or_404(self, protocol_id: UUID) -> dict:
        row = await self.repo.get(protocol_id)
        if not row:
            raise NotFoundError("Treatment protocol not found", code="PROTOCOL_NOT_FOUND")
        return row

    async def get_detail(self, protocol_id: UUID, ctx: RequestContext) -> dict:
        row = await self.get_or_404(protocol_id)
        # assert_clinic_scope has no patient branch (patient passes through
        # like super_admin) — without this, any patient could read any other
        # patient's protocol in the same clinic just by guessing the UUID.
        assert_owns_profile(ctx, row["patient_id"])
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])

        slug = _slug_for_modality(row["modality"])
        custom_montage_id = row.get("custom_montage_id")

        # A protocol has either a catalogue placement/dosing pair or a
        # custom_montage_id (54, chk_protocol_plan_one_placement) - never
        # both, so hydrating one side and leaving the other None is correct
        # by construction, not a fallback.
        if custom_montage_id:
            placement_id = None
            dosing_id = None
            placement = None
            dosing = None
            custom_montage = await self.custom_montages.get(custom_montage_id)
        else:
            placement_id = row.get(placement_pk(slug))
            dosing_id = row.get(dosing_pk(slug))
            placement = await self.catalogue.get_placement(slug, placement_id) if placement_id else None
            if placement:
                placement["summary"] = _placement_summary(placement)
            dosing = await self.catalogue.get_dosing(slug, dosing_id) if dosing_id else None
            custom_montage = None

        all_rows = await self.sessions.list_for_protocol(protocol_id)
        detail = dict(row)
        detail["placement_id"] = placement_id
        detail["placement_summary"] = _placement_summary(placement) if placement else None
        detail["dosing_id"] = dosing_id
        detail["placement"] = placement
        detail["dosing"] = dosing
        detail["custom_montage_id"] = custom_montage_id
        detail["custom_montage"] = custom_montage
        detail["conditions"] = await self.details.list_conditions(protocol_id)
        detail["diagnoses"] = await self.details.list_diagnoses(protocol_id)
        detail["scales"] = await self.details.list_scales(protocol_id)
        detail["sessions"] = [r for r in all_rows if r["appointment_type"] == _TYPE_DEVICE_SESSION]
        detail["follow_ups"] = [r for r in all_rows if r["appointment_type"] == _TYPE_FOLLOW_UP]
        return detail

    async def list(
        self, ctx: RequestContext, *, include_sessions: bool = False, sessions_all_types: bool = False, **filters
    ) -> builtins.list[dict]:
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
        rows = await self.repo.list(**filters)
        if include_sessions:
            # Same device-session list get_detail() returns, for every row in
            # one query — the doctor Sessions page used to fetch the full
            # detail of each protocol just for this (API audit F-034).
            # sessions_all_types: every appointment of the protocol, same as
            # GET /treatment-protocols/{id}/sessions (doctor Reports KPIs, F-037).
            by_protocol = await self.sessions.list_for_protocols(
                [str(r["protocol_id"]) for r in rows], appointment_type=None if sessions_all_types else _TYPE_DEVICE_SESSION
            )
            for r in rows:
                r["sessions"] = by_protocol.get(str(r["protocol_id"]), [])
        return rows

    async def list_sessions(self, protocol_id: UUID, ctx: RequestContext, *, appointment_type: str | None = None) -> builtins.list[dict]:
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        return await self.sessions.list_for_protocol(protocol_id, appointment_type=appointment_type)

    # -- create ------------------------------------------------------------

    async def readiness(self, patient_profile_id: UUID) -> dict:
        """Whether the patient's first treatment protocol may be started.
        All three of anamnesis (required questions), PRS and a doctor's note
        must be done in the initial consultation.
        ponytail: owner allowed "anamnesis + any one of PRS / notes" too; to
        relax, drop prs/doctor_notes from `missing` when the other is done."""
        row = await self.repo.initial_consultation_readiness(patient_profile_id)
        missing = [
            m
            for m, done in (
                ("initial_consultation", row["appointment_id"] is not None),
                ("anamnesis", not row["missing_questions"]),
                ("prs", row["prs_completed"]),
                ("doctor_notes", row["notes_count"] > 0),
            )
            if not done
        ]
        gate_applies = not row["has_started_treatment"]
        return {**row, "missing": missing, "gate_applies": gate_applies, "ready": not gate_applies or not missing}

    async def readiness_for_patient(self, patient_id: UUID, ctx: RequestContext) -> dict:
        """readiness() by public patient id, clinic-scoped for the caller."""
        from app.modules.patients.repository import PatientRepository

        patient = await PatientRepository(self.session).get(patient_id)
        if not patient:
            raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
        await assert_clinic_scope(ctx, self.session, patient["primary_clinic_id"])
        return await self.readiness(patient["profile_id"])

    async def create(self, body: s.ProtocolCreate, ctx: RequestContext) -> dict:
        """Step 8. Creates the protocol and every appointment in one
        transaction, so a partially-booked course can never exist."""
        # 106: a clinical assistant amends, never creates. A protocol with no
        # supersedes_protocol_id starts a new lineage (version x.0), which is
        # a prescription only a doctor issues. rls_protocol_plan_insert
        # refuses the same row; this names the reason as a 403.
        if ctx.role == "clinical_assistant" and body.supersedes_protocol_id is None:
            raise PermissionError_(
                "A clinical assistant can amend an existing protocol but cannot create a new one",
                code="CA_CANNOT_CREATE_PROTOCOL",
            )
        # 45 re-parented the protocol onto protocol_instances; 48 made that
        # the only parent. Resolve it into patient/doctor/clinic once.
        parent = await self._resolve_parent(body)
        await assert_clinic_scope(ctx, self.session, parent["clinic_id"])

        # The patient's first prescription waits on the initial consultation's
        # anamnesis, PRS and doctor's note. Amendments and later protocols
        # (follow-up territory) are not gated.
        if body.supersedes_protocol_id is None:
            readiness = await self.readiness(parent["patient_id"])
            if not readiness["ready"]:
                raise BusinessRuleError(
                    "Before starting a treatment protocol, in the initial consultation: "
                    + "; ".join(_PREREQUISITE_LABELS[m] for m in readiness["missing"]),
                    code="PROTOCOL_PREREQUISITES_MISSING",
                    details=[
                        {"field": m, "message": _PREREQUISITE_LABELS[m]}
                        | ({"missing_questions": readiness["missing_questions"]} if m == "anamnesis" else {})
                        for m in readiness["missing"]
                    ],
                )

        device = await self._prescribable_device(body.device_id, parent["clinic_id"])
        slug = _slug_for_modality(device["modality"])
        montage_columns = await self._montage_columns(
            slug=slug,
            device_id=body.device_id,
            clinic_id=parent["clinic_id"],
            placement_id=body.placement_id,
            dosing_id=body.dosing_id,
            custom_montage_id=body.custom_montage_id,
        )

        if body.device_unit_id is not None and not await self.repo.device_unit_belongs_to_clinic_device(
            parent["clinic_id"], body.device_id, body.device_unit_id
        ):
            raise BusinessRuleError(
                "That unit is not an active unit of this device at this clinic.",
                code="DEVICE_UNIT_NOT_AT_CLINIC",
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

        # Major.minor versioning: an amendment inherits its target's major and
        # adds one to its minor; anything else starts a new lineage at the
        # instance's next major. The target must be the current, un-amended
        # head of its own lineage — amending twice or amending something
        # already cancelled/completed would silently fork the history.
        if body.supersedes_protocol_id is not None:
            target = await self.repo.get(body.supersedes_protocol_id)
            if not target:
                raise NotFoundError("Protocol to amend not found", code="SUPERSEDES_NOT_FOUND")
            if str(target["instance_id"]) != str(body.instance_id):
                raise ValidationError(
                    "Cannot amend a protocol from a different instance",
                    code="SUPERSEDES_INSTANCE_MISMATCH",
                )
            if target["status"] not in ("draft", "active"):
                raise ConflictError(
                    "Only the current draft/active version of a protocol can be amended",
                    code="SUPERSEDES_NOT_CURRENT",
                )
            version_major = target["version_major"]
            version_minor = target["version_minor"] + 1
        else:
            version_major = await self.repo.next_major_version(body.instance_id)
            version_minor = 0

        # Map the caller's device-agnostic placement_id/dosing_id onto the
        # correct pair of the six nullable catalogue FK columns, or write
        # custom_montage_id alone with both catalogue pairs left NULL. The
        # exactly-one/biconditional CHECKs (54) then hold by construction.
        payload: dict[str, Any] = {
            "instance_id": str(body.instance_id),
            "device_id": str(body.device_id),
            "device_unit_id": str(body.device_unit_id) if body.device_unit_id else None,
            "set_by": ctx.user_id,
            **montage_columns,
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
            "prescribed_tvns_wavelength": body.prescribed_tvns_wavelength,
            "prescribed_tvns_pattern": body.prescribed_tvns_pattern,
            "prescribed_tvns_strength_pct": body.prescribed_tvns_strength_pct,
            "prescribed_tvns_frequency_hz": body.prescribed_tvns_frequency_hz,
            "prescribed_tvns_pulse_width_us": body.prescribed_tvns_pulse_width_us,
            "prescribed_tvns_duration_min": body.prescribed_tvns_duration_min,
            "prescribed_tvns_ramp_up_sec": body.prescribed_tvns_ramp_up_sec,
            "prescribed_tvns_ramp_down_sec": body.prescribed_tvns_ramp_down_sec,
            "sessions_per_week": body.sessions_per_week,
            "sessions_per_day": body.sessions_per_day,
            "supersedes_protocol_id": (str(body.supersedes_protocol_id) if body.supersedes_protocol_id else None),
            "version_major": version_major,
            "version_minor": version_minor,
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
                sessions_per_day=body.sessions_per_day,
                follow_up_every_n=body.follow_up_every_n,
                skip_dates=body.skip_dates,
                extra_dates=body.extra_dates,
            )
        )

        # Same transaction as the new row: the amended protocol stops being
        # editable/current the instant its replacement exists, never a moment
        # where both read as the live version.
        #
        # Relink whatever the new schedule reproduces unchanged (same date,
        # same session/follow-up number) instead of cancelling the old row
        # and inserting a fresh one for it — cuts down on duplicate
        # appointment rows for a course that mostly carries over, and a
        # relinked row keeps its original appointment_id, so a doctor who
        # already has that row open (or a patient with a notification
        # pointing at it) doesn't lose that continuity over an amendment
        # that only changed, say, the current_ma. This MUST run before
        # cancel_planned below: relink only matches still-'planned' rows, so
        # if cancel_planned ran first there would be nothing left to relink.
        # Whatever relink doesn't claim (moved/added/dropped dates) falls
        # through to cancel_planned exactly as a non-relinked amendment
        # always has.
        relinked_keys: set[tuple[Any, int, str]] = set()
        if body.supersedes_protocol_id is not None:
            # appointment_type is part of the key, not just date+number: a
            # device session's recomputed date can coincidentally equal an
            # unrelated old follow-up's own (date, number) — without the type
            # tag here, relink_planned_matching's SQL (also type-checked, see
            # its docstring) would never be reachable, since the mismatch
            # would already be baked into what candidates this call sends it.
            candidates = [(item["planned_date"], item["session_number"], _TYPE_DEVICE_SESSION) for item in preview["sessions"]] + [
                (item["planned_date"], item["after_session_number"], _TYPE_FOLLOW_UP) for item in preview["follow_ups"]
            ]
            relinked = await self.sessions.relink_planned_matching(body.supersedes_protocol_id, protocol_id, candidates)
            if relinked:
                device_session_ids = [r["appointment_id"] for r in relinked if r["appointment_type"] == _TYPE_DEVICE_SESSION]
                follow_up_ids = [r["appointment_id"] for r in relinked if r["appointment_type"] == _TYPE_FOLLOW_UP]
                await self.device_sessions.relink_protocol(device_session_ids, protocol_id)
                await self.followups.relink_protocol(follow_up_ids, protocol_id)
                relinked_keys = {(r["appointment_date"], r["session_number"], r["appointment_type"]) for r in relinked}

            # cancel_planned is the same call ProtocolService.cancel() makes;
            # an amendment supersedes the old course exactly like a
            # cancellation would for anything relink didn't already claim.
            await self.sessions.cancel_planned(body.supersedes_protocol_id, reason="Superseded by protocol amendment")
            await self.repo.set_status(body.supersedes_protocol_id, "superseded")

            # PRS-freeze: any pending scale on an already-completed session
            # under the old protocol can no longer be answered — closes the
            # gap where a very late answer would retroactively change an
            # already-superseded (and possibly already-reported-on) protocol
            # version's outcome numbers. Same transaction as the supersede
            # above, so there's no window for a late submission to land
            # in between. A session still in flight right now is deliberately
            # not touched here — see DeviceSessionScaleRepository.
            # freeze_pending_for_session for that edge case.
            from app.modules.device_sessions.repository import DeviceSessionScaleRepository

            await DeviceSessionScaleRepository(self.session).freeze_pending_for_protocol(body.supersedes_protocol_id)

        counts = await self._generate_appointments(protocol_id, parent, preview, device_id=body.device_id, skip_keys=relinked_keys)
        counts["sessions_relinked"] = len(relinked_keys)

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
                "sessions_per_day": body.sessions_per_day,
                "conditions_recorded": conditions_written,
                "diagnoses_recorded": diagnoses_written,
                "scales_assigned": scales_written,
                **counts,
            },
        )
        return await self.get_or_404(protocol_id)

    async def _prescribable_device(self, device_id: UUID, clinic_id: Any) -> dict:
        """A device a protocol may be written for: active, enabled for
        prescribing, and owned by the clinic."""
        device = await self.catalogue_svc.get_device_or_404(device_id)
        if not device["is_active"]:
            raise BusinessRuleError("Device is not active", code="DEVICE_INACTIVE")
        if device["phase"] != 1:
            raise BusinessRuleError(
                f"{device['device_name']} is catalogued but not yet enabled for prescribing (phase {device['phase']}).",
                code="DEVICE_NOT_ENABLED",
            )
        # The clinic must actually own the machine. trg_check_device_available
        # _at_clinic (37) enforces this, but a readable 422 beats a raised
        # PL/pgSQL exception, and the doctor needs to know it is an inventory
        # problem rather than a prescribing one.
        if not await self.repo.clinic_has_device(clinic_id, device_id):
            raise BusinessRuleError(
                f"{device['device_name']} is not available at this clinic.",
                code="DEVICE_NOT_AT_CLINIC",
            )
        return device

    async def _montage_columns(
        self,
        *,
        slug: str,
        device_id: UUID,
        clinic_id: Any,
        placement_id: UUID | None,
        dosing_id: UUID | None,
        custom_montage_id: UUID | None,
    ) -> dict:
        """Validates the montage and maps it onto protocol_plan's columns:
        the device family's own placement/dosing pair, or custom_montage_id
        alone. Callers have already enforced exactly-one-source."""
        if custom_montage_id is not None:
            custom_montage = await self.custom_montages.get(custom_montage_id)
            if not custom_montage:
                raise NotFoundError("Custom montage not found", code="MONTAGE_NOT_FOUND")
            if not custom_montage["is_active"]:
                raise BusinessRuleError("Custom montage has been retired and can no longer be prescribed", code="MONTAGE_INACTIVE")
            if str(custom_montage["device_id"]) != str(device_id):
                raise ValidationError("Custom montage belongs to a different device", code="MONTAGE_DEVICE_MISMATCH")
            # A custom montage is a clinical record any staff at its clinic
            # may prescribe with, not just its author - unlike deactivate()
            # (CustomMontageService.deactivate), which is an ownership
            # action and stays author-or-admin. clinic_id is nullable on
            # protocol_custom_montages (superadmin-authored montages may have
            # none); only refuse a montage authored at a DIFFERENT clinic.
            if custom_montage["clinic_id"] is not None and str(custom_montage["clinic_id"]) != str(clinic_id):
                raise ValidationError("Custom montage belongs to a different clinic", code="MONTAGE_CLINIC_MISMATCH")
            return {"custom_montage_id": str(custom_montage_id)}

        assert placement_id is not None
        assert dosing_id is not None
        # Same-device consistency. The schema enforces this with a trigger
        # because a CHECK cannot read another table; checking here first
        # turns a raw PL/pgSQL exception into a readable 422.
        placement = await self.catalogue.get_placement(slug, placement_id)
        if not placement:
            raise NotFoundError("Placement not found for this device family", code="PLACEMENT_NOT_FOUND")
        if str(placement["device_id"]) != str(device_id):
            raise ValidationError("Placement belongs to a different device", code="PLACEMENT_DEVICE_MISMATCH")
        dosing = await self.catalogue.get_dosing(slug, dosing_id)
        if not dosing:
            raise NotFoundError("Dosing not found for this device family", code="DOSING_NOT_FOUND")
        if str(dosing["device_id"]) != str(device_id):
            raise ValidationError("Dosing belongs to a different device", code="DOSING_DEVICE_MISMATCH")
        return {placement_pk(slug): str(placement_id), dosing_pk(slug): str(dosing_id)}

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
        """Checked against reference.prs_scales (51), which is the catalogue the
        questionnaire engine renders from — a scale it does not have cannot be
        released to the patient, so accepting the id would store a task that
        never appears."""
        ids = [sc.scale_id for sc in items]
        if not ids:
            return
        found = await self.catalogue.get_prs_scales_by_ids(ids)
        missing = set(ids) - {str(r["prs_scale_id"]) for r in found}
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

    async def _generate_appointments(
        self, protocol_id: UUID, parent: dict, preview: dict, *, device_id: UUID, skip_keys: set[tuple[Any, int, str]] | None = None
    ) -> dict:
        """Writes the whole course onto the appointments spine, and — 47 — a
        matching protocol_device_sessions / protocol_followup row alongside
        each one, carrying the appointment_id it just got back.

        Every appointments row is 'planned' with a date and no time - exactly
        the state 31 describes ("doctor set a DATE at protocol setup. No slot
        yet, no hold"). The patient picks a time later, moving the row to
        Every row carries the responsible doctor. A device session is run by
        a clinical assistant, so 52 excluded that type from
        excl_doctor_overlap: the prescriber is recorded without their diary
        being booked for work they do not attend.

        booked_by / booked_by_role stay NULL. They mean "who confirmed the
        booking" - the receptionist or the patient, at payment time - and a
        planned row has neither a slot nor a payment yet.
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
        # The doctor responsible for the course. Set on every generated row,
        # including device sessions: 52 narrowed excl_doctor_overlap to
        # exclude device_session, so recording the prescriber no longer books
        # out their calendar for work a clinical assistant performs.
        doctor_id = parent.get("doctor_id")
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

        skip_keys = skip_keys or set()
        for item in preview["sessions"]:
            # Amendment already relinked the old appointment row that covers
            # this exact (date, session_number, type) — inserting a second
            # one here would duplicate it and collide with
            # uq_appointments_protocol_session. Type is part of the key: see
            # relink_planned_matching's docstring for why date+number alone
            # isn't enough to tell a device session from a follow-up.
            if (item["planned_date"], item["session_number"], _TYPE_DEVICE_SESSION) in skip_keys:
                continue
            appt = await self.sessions.create(
                {
                    "clinic_id": str(clinic_id),
                    "patient_id": str(parent["patient_id"]),
                    "doctor_id": str(doctor_id) if doctor_id else None,
                    "protocol_id": str(protocol_id),
                    "clinic_device_id": str(clinic_device_id),
                    "appointment_date": item["planned_date"],
                    "appointment_type": _TYPE_DEVICE_SESSION,
                    "session_number": item["session_number"],
                    "status": _STATUS_PLANNED,
                    # booked_by means "who confirmed the booking" - a
                    # receptionist or the patient, at payment time. A planned
                    # row has no slot and no payment, so nobody booked it;
                    # writing the prescriber here claimed a doctor took a
                    # payment (52). The booking path fills these in on
                    # transition to selected/paid.
                    "booked_by": None,
                    "booked_by_role": None,
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
            if (item["planned_date"], item["after_session_number"], _TYPE_FOLLOW_UP) in skip_keys:
                continue
            appt = await self.sessions.create(
                {
                    "clinic_id": str(clinic_id),
                    "patient_id": str(parent["patient_id"]),
                    # A follow-up IS a doctor consultation, so this one does
                    # carry the doctor - and no device: the same CHECK that
                    # requires clinic_device_id on a session forbids it here.
                    "doctor_id": str(doctor_id) if doctor_id else None,
                    "protocol_id": str(protocol_id),
                    "appointment_date": item["planned_date"],
                    "appointment_type": _TYPE_FOLLOW_UP,
                    "session_number": item["after_session_number"],
                    "status": _STATUS_PLANNED,
                    "booked_by": None,
                    "booked_by_role": None,
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
        sessions_skipped = sum(
            1 for item in preview["sessions"] if (item["planned_date"], item["session_number"], _TYPE_DEVICE_SESSION) in skip_keys
        )
        follow_ups_skipped = sum(
            1 for item in preview["follow_ups"] if (item["planned_date"], item["after_session_number"], _TYPE_FOLLOW_UP) in skip_keys
        )
        return {
            "sessions_created": len(preview["sessions"]) - sessions_skipped,
            "follow_ups_created": len(preview["follow_ups"]) - follow_ups_skipped,
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
        # Who edited which field. The audit trigger stores the whole old/new
        # row, but audit_logs is admin-only; this is the copy the doctor's
        # activity timeline reads (emit_event stamps actor id and role).
        changes = {k: {"from": row.get(k), "to": v} for k, v in fields.items() if row.get(k) != v}
        if updated and changes:
            await emit_event(
                self.session,
                aggregate_type="treatment_protocol",
                aggregate_id=protocol_id,
                event_type="treatment_protocol.updated",
                payload=jsonable_encoder({"protocol_id": str(protocol_id), "instance_id": str(row["instance_id"]), "changes": changes}),
            )
        return updated or row

    async def amend(self, protocol_id: UUID, body: s.ProtocolAmend, ctx: RequestContext) -> dict:
        """'Modify protocol': a new minor version that copies the current one,
        applies only what the request sent, takes over the course's remaining
        sessions and goes live — one transaction. Sessions already delivered
        stay on the version they were delivered under. See ProtocolAmend for
        when the remaining sessions are re-planned instead of carried over."""
        target = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, target["clinic_id"])
        if target["status"] != "active":
            raise BusinessRuleError(
                f"Only an active protocol can be modified (current status: '{target['status']}')",
                code="PROTOCOL_NOT_ACTIVE",
            )
        raw = await self.repo.get_plan_row(protocol_id)
        assert raw is not None

        sent = body.model_dump(exclude_unset=True)
        # A field explicitly sent as null keeps its current value — clearing a
        # prescribed setting is never a modification, activation would refuse it.
        sent = {k: v for k, v in sent.items() if v is not None}
        lists = {k: sent.pop(k) for k in ("conditions", "diagnosis_ids", "scales") if k in sent}
        schedule_only = {k: sent.pop(k) for k in ("start_date", "skip_dates", "extra_dates") if k in sent}
        for k in ("reason", "clinical_note"):
            sent.pop(k, None)
        montage_sent = {k: sent.pop(k) for k in ("placement_id", "dosing_id", "custom_montage_id") if k in sent}

        # -- device and montage --------------------------------------------
        device_id = sent.get("device_id", target["device_id"])
        device_changed = str(device_id) != str(target["device_id"])
        device = await self._prescribable_device(device_id, target["clinic_id"]) if device_changed else None
        modality = device["modality"] if device else target["modality"]
        slug = _slug_for_modality(modality)
        if device_changed and not montage_sent:
            raise ValidationError("A new device needs its own montage and dosing", code="MONTAGE_REQUIRED_FOR_DEVICE")
        merged = {**raw, **sent}
        if device_changed and "device_unit_id" not in sent:
            merged["device_unit_id"] = None  # the old unit belongs to the old device
        if montage_sent:
            columns = await self._montage_columns(
                slug=slug,
                device_id=device_id,
                clinic_id=target["clinic_id"],
                placement_id=montage_sent.get("placement_id"),
                dosing_id=montage_sent.get("dosing_id"),
                custom_montage_id=montage_sent.get("custom_montage_id"),
            )
            # One montage source at a time (chk_protocol_plan_one_placement):
            # clear every family's pair and the custom montage, then set the new one.
            for family in ("tdcs", "hd_tdcs", "tvns", "tps", "rtms", "other"):
                merged[f"{family}_placement_id"] = None
                merged[f"{family}_dosing_id"] = None
            merged["custom_montage_id"] = None
            merged.update(columns)
        if merged.get("device_unit_id") and (device_changed or "device_unit_id" in sent):
            if not await self.repo.device_unit_belongs_to_clinic_device(target["clinic_id"], device_id, merged["device_unit_id"]):
                raise BusinessRuleError("That unit is not an active unit of this device at this clinic.", code="DEVICE_UNIT_NOT_AT_CLINIC")

        # -- dose -----------------------------------------------------------
        is_tvns = modality == "tVNS"
        if is_tvns and ({"prescribed_current_ma", "prescribed_duration_min", "ramp_seconds"} & sent.keys()):
            raise ValidationError("A tVNS protocol is dosed with the tVNS fields", code="DOSE_FIELDS_MISMATCH")
        if not is_tvns and any(k.startswith("prescribed_tvns_") for k in sent):
            raise ValidationError(f"A {modality} protocol has no tVNS dose fields", code="DOSE_FIELDS_MISMATCH")
        if device_changed:
            # The other family's dose doesn't carry over to a new modality;
            # activation then insists the new family's dose is complete.
            stale = (
                ("prescribed_current_ma", "prescribed_duration_min")
                if is_tvns
                else tuple(k for k in raw if k.startswith("prescribed_tvns_"))
            )
            for k in stale:
                if k not in sent:
                    merged[k] = None
        if is_tvns and merged.get("prescribed_tvns_pattern") != "intermittent":
            if "prescribed_tvns_ramp_up_sec" in sent or "prescribed_tvns_ramp_down_sec" in sent:
                raise ValidationError(
                    "prescribed_tvns_ramp_up_sec/prescribed_tvns_ramp_down_sec only apply to pattern='intermittent'",
                    code="TVNS_RAMP_NOT_INTERMITTENT",
                )
            # Moving off intermittent drops the ramps that belonged to it.
            merged["prescribed_tvns_ramp_up_sec"] = None
            merged["prescribed_tvns_ramp_down_sec"] = None
        if merged.get("follow_up_every_n") and merged["follow_up_every_n"] > merged["session_count"]:
            raise ValidationError("follow_up_every_n cannot exceed session_count", code="FOLLOW_UP_TOO_LARGE")

        # -- child lists ----------------------------------------------------
        if "conditions" in lists:
            await self._assert_conditions_exist(body.conditions or [])
        if "diagnosis_ids" in lists:
            await self._assert_diagnoses_exist(body.diagnosis_ids or [])
        if "scales" in lists:
            await self._assert_scales_exist(body.scales or [])

        changes = {k: {"from": raw.get(k), "to": merged.get(k)} for k in merged if k in raw and raw.get(k) != merged.get(k)}
        if "conditions" in lists:
            before_c = sorted(str(r["condition_id"] or r["other_text"]) for r in await self.details.list_conditions(protocol_id))
            after_c = sorted(str(c.condition_id or c.other_text) for c in body.conditions or [])
            if before_c != after_c:
                changes["conditions"] = {"from": before_c, "to": after_c}
        if "diagnosis_ids" in lists:
            before_dx = sorted(str(r["diagnosis_id"]) for r in await self.details.list_diagnoses(protocol_id))
            after_dx = sorted(str(d) for d in body.diagnosis_ids or [])
            if before_dx != after_dx:
                changes["diagnoses"] = {"from": before_dx, "to": after_dx}
        if "scales" in lists:
            before_sc = sorted((str(r["prs_scale_id"] or r["scale_id"]), r["cadence"]) for r in await self.details.list_scales(protocol_id))
            after_sc = sorted((sc.scale_id, sc.cadence) for sc in body.scales or [])
            if before_sc != after_sc:
                changes["scales"] = {"from": before_sc, "to": after_sc}
        replan = (
            device_changed
            or bool(schedule_only)
            or any(k in changes for k in ("session_count", "sessions_per_week", "sessions_per_day", "follow_up_every_n"))
        )
        for k, v in schedule_only.items():
            changes[k] = {"from": None, "to": v}
        if not changes:
            raise ValidationError("Nothing was changed", code="AMENDMENT_NO_CHANGES")

        # -- the new version ------------------------------------------------
        # Everything that identifies the old row or its lifecycle is not copied.
        payload = {
            k: v
            for k, v in merged.items()
            if k not in ("protocol_id", "status", "activated_at", "completed_at", "created_at", "updated_at")
        }
        payload.update(
            {
                "set_by": ctx.user_id,
                "status": "draft",
                "supersedes_protocol_id": str(protocol_id),
                "version_major": target["version_major"],
                "version_minor": target["version_minor"] + 1,
                "notes": f"Reason: {body.reason} — {body.clinical_note}",
            }
        )
        try:
            created = await self.repo.create(payload)
        except IntegrityError as exc:  # pragma: no cover - surfaced as 409
            raise ConflictError("Protocol violates a database constraint", code="PROTOCOL_CONSTRAINT") from exc
        new_id = created["protocol_id"]

        await self.details.copy_from(
            source_id=protocol_id,
            target_id=new_id,
            conditions="conditions" not in lists,
            scales="scales" not in lists,
            diagnoses="diagnosis_ids" not in lists,
        )
        if "conditions" in lists:
            await self.details.add_conditions(new_id, [c.model_dump() for c in body.conditions or []])
        if "diagnosis_ids" in lists:
            await self.details.add_diagnoses(new_id, body.diagnosis_ids or [])
        if "scales" in lists:
            await self.details.add_scales(new_id, [sc.model_dump() for sc in body.scales or []])

        # -- the remaining sessions -----------------------------------------
        if replan:
            moved = await self._replan_remaining(
                old_id=protocol_id,
                new_id=new_id,
                target=target,
                plan=merged,
                device_id=device_id,
                device_changed=device_changed,
                start_date=schedule_only.get("start_date"),
                skip_dates=schedule_only.get("skip_dates", []),
                extra_dates=schedule_only.get("extra_dates", []),
            )
        else:
            relinked = await self.sessions.relink_unstarted(protocol_id, new_id)
            await self.device_sessions.relink_protocol(
                [r["appointment_id"] for r in relinked if r["appointment_type"] == _TYPE_DEVICE_SESSION], new_id
            )
            await self.followups.relink_protocol(
                [r["appointment_id"] for r in relinked if r["appointment_type"] == _TYPE_FOLLOW_UP], new_id
            )
            moved = len(relinked)
        if not moved:
            raise BusinessRuleError(
                "This course has no sessions left to run, so there is nothing to modify. Complete it or start a new protocol.",
                code="PROTOCOL_NOTHING_REMAINING",
            )
        await self.sessions.cancel_planned(protocol_id, reason="Superseded by protocol amendment")
        await self.repo.set_status(protocol_id, "superseded")

        from app.modules.device_sessions.repository import DeviceSessionScaleRepository

        await DeviceSessionScaleRepository(self.session).freeze_pending_for_protocol(protocol_id)

        activated = await self.activate(new_id, ctx)
        await emit_event(
            self.session,
            aggregate_type="treatment_protocol",
            aggregate_id=new_id,
            event_type="treatment_protocol.amended",
            payload=jsonable_encoder(
                {
                    "protocol_id": str(new_id),
                    "supersedes_protocol_id": str(protocol_id),
                    "instance_id": str(target["instance_id"]),
                    "reason": body.reason,
                    "changes": changes,
                    "replanned": replan,
                    "sessions_on_new_version": moved,
                }
            ),
        )
        return activated

    async def _replan_remaining(
        self,
        *,
        old_id: UUID,
        new_id: UUID,
        target: dict,
        plan: dict,
        device_id: UUID,
        device_changed: bool,
        start_date: dt.date | None,
        skip_dates: builtins.list[dt.date],
        extra_dates: builtins.list[dt.date],
    ) -> int:
        """Re-plans what is left of the course on the new version.

        session_count is the whole course: the sessions delivered so far
        count toward it. Sessions the patient has booked keep their date and
        slot; the rest are re-dated from start_date on the new cadence. All
        remaining sessions are then numbered in date order after the last
        session that took place. The old version's unbooked rows are
        cancelled by the caller. Returns how many appointments the new
        version now holds."""
        progress = await self.sessions.lineage_progress(target["instance_id"], target["version_major"])
        booked = await self.sessions.list_booked(old_id)
        booked_sessions = [b for b in booked if b["appointment_type"] == _TYPE_DEVICE_SESSION]
        booked_follow_ups = [b for b in booked if b["appointment_type"] == _TYPE_FOLLOW_UP]
        if device_changed and booked_sessions:
            raise BusinessRuleError(
                f"{len(booked_sessions)} upcoming session(s) are already booked on the current device. "
                "Reschedule or cancel them before changing the device.",
                code="BOOKED_SESSIONS_ON_DEVICE",
            )

        to_generate = plan["session_count"] - progress["delivered"] - len(booked_sessions)
        if to_generate < 0:
            raise ValidationError(
                f"The course already has {progress['delivered']} delivered and {len(booked_sessions)} booked sessions — "
                f"session_count can't be less than {progress['delivered'] + len(booked_sessions)}.",
                code="SESSION_COUNT_TOO_LOW",
            )
        today = dt.date.today()
        start = start_date or today
        if start < today:
            raise ValidationError("The remaining sessions can't start in the past", code="START_DATE_IN_PAST")

        weekdays = _WEEKDAYS_FOR[plan["sessions_per_week"]]
        new_dates: builtins.list[dt.date] = []
        if to_generate:
            # Bypasses SchedulePreviewRequest's 10-session minimum on purpose:
            # this is the rest of a course, not a course. A day that already
            # holds a booked session is skipped whole, even when sessions_per_day
            # would leave room on it — never over the daily limit, at the cost
            # of a slightly longer course.
            preview = ScheduleService.build(
                s.SchedulePreviewRequest.model_construct(
                    start_date=start,
                    session_count=to_generate,
                    sessions_per_week=plan["sessions_per_week"],
                    sessions_per_day=plan.get("sessions_per_day") or 1,
                    follow_up_every_n=None,
                    skip_dates=[*skip_dates, *(b["appointment_date"] for b in booked_sessions)],
                    extra_dates=extra_dates,
                )
            )
            new_dates = [item["planned_date"] for item in preview["sessions"]]

        timeline = sorted(
            [(b["appointment_date"], 0, b["appointment_id"]) for b in booked_sessions] + [(d, 1, None) for d in new_dates],
            key=lambda x: (x[0], x[1]),
        )
        numbered = [
            (number, date, appointment_id) for number, (date, _, appointment_id) in enumerate(timeline, start=progress["last_number"] + 1)
        ]

        moves = [(appointment_id, number) for number, _, appointment_id in numbered if appointment_id is not None]
        await self.sessions.move_to_protocol(new_id, moves)
        await self.device_sessions.move_to_protocol(new_id, moves)
        # A booked follow-up keeps its slot and number.
        await self.sessions.move_to_protocol(new_id, [(b["appointment_id"], b["session_number"]) for b in booked_follow_ups])
        await self.followups.relink_protocol([b["appointment_id"] for b in booked_follow_ups], new_id)

        every_n = plan.get("follow_up_every_n")
        kept_follow_ups = {b["session_number"] for b in booked_follow_ups}
        follow_ups = [
            {"after_session_number": number, "planned_date": ScheduleService.follow_up_date(date, weekdays, set(skip_dates))}
            for number, date, _ in numbered
            if every_n and number % every_n == 0 and number not in kept_follow_ups
        ]
        sessions = [{"session_number": number, "planned_date": date} for number, date, appointment_id in numbered if appointment_id is None]
        parent = await self._resolve_parent(s.ProtocolCreate.model_construct(instance_id=target["instance_id"]))
        counts = await self._generate_appointments(new_id, parent, {"sessions": sessions, "follow_ups": follow_ups}, device_id=device_id)
        return len(moves) + len(booked_follow_ups) + counts["sessions_created"] + counts["follow_ups_created"]

    async def activate(self, protocol_id: UUID, ctx: RequestContext) -> dict:
        """The 'Push' action from step 8."""
        row = await self.get_or_404(protocol_id)
        await assert_clinic_scope(ctx, self.session, row["clinic_id"])
        # 106: a clinical assistant may push only an amendment they authored.
        # Activating a doctor's first draft would be issuing the protocol.
        if ctx.role == "clinical_assistant" and (row.get("supersedes_protocol_id") is None or str(row["set_by"]) != str(ctx.user_id)):
            raise PermissionError_(
                "A clinical assistant can only activate their own amendment",
                code="CA_CANNOT_ACTIVATE_PROTOCOL",
            )
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

        # fn_check_protocol_prescription_complete (39, modality-aware since
        # 91/92) refuses this transition if the dose is incomplete. Checking
        # here first names the missing fields as a 422 the UI can map back
        # onto step 5, instead of a raised PL/pgSQL exception surfacing as a
        # 500. Must mirror the trigger's modality split exactly: tDCS/HD-tDCS
        # and tVNS both prescribe via their own plain, freely-typed columns
        # (prescribed_current_ma/... for tDCS, prescribed_tvns_.../ for tVNS
        # — 92, tVNS has no "current_ma" concept so it needed its own set);
        # TPS/rTMS/other still prescribe via their catalogue dosing_id FK.
        missing: list[str] = []
        if row["modality"] in ("tDCS", "HD-tDCS"):
            if row.get("prescribed_current_ma") is None:
                missing.append("prescribed_current_ma")
            if row.get("prescribed_duration_min") is None:
                missing.append("prescribed_duration_min")
        elif row["modality"] == "tVNS":
            for field in (
                "prescribed_tvns_wavelength",
                "prescribed_tvns_pattern",
                "prescribed_tvns_strength_pct",
                "prescribed_tvns_frequency_hz",
                "prescribed_tvns_pulse_width_us",
                "prescribed_tvns_duration_min",
            ):
                if row.get(field) is None:
                    missing.append(field)
        elif not row.get("custom_montage_id"):
            slug = _slug_for_modality(row["modality"])
            if not row.get(f"{slug}_dosing_id"):
                missing.append("dosing_id")
        if row.get("sessions_per_week") is None:
            missing.append("sessions_per_week")
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
        if await self.sessions.has_pending(protocol_id):
            raise BusinessRuleError(
                "This protocol still has device sessions or follow-ups that aren't completed, "
                "cancelled, or marked no-show — resolve them before marking the course complete.",
                code="PROTOCOL_HAS_PENDING_SESSIONS",
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
    """core.protocol_instances (45, absorbed treatment_cycles in 58) — one
    episode of care. The Treatment Protocol tab lists instances; each
    instance holds the protocols prescribed within it, including amendments
    (a new protocol superseding an earlier one).
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
        patient_profile_id = await _resolve_patient_profile_id(self.session, body.patient_id)
        doctor_profile_id = await _resolve_doctor_profile_id(self.session, body.doctor_id)
        await assert_clinic_scope(ctx, self.session, body.clinic_id)

        # A patient may hold several open instances at once (90 dropped
        # uq_protocol_instances_one_active); their device sessions are kept
        # apart by excl_patient_device_session_overlap instead.
        number = await self.repo.next_instance_number(patient_profile_id)
        try:
            created = await self.repo.create(
                {
                    "patient_id": str(patient_profile_id),
                    "doctor_id": str(doctor_profile_id),
                    "ca_id": str(body.ca_id) if body.ca_id else None,
                    "clinic_id": str(body.clinic_id),
                    "instance_type": body.instance_type,
                    "created_by": ctx.user_id,
                    "instance_number": number,
                    "status": "draft",
                    "notes": body.notes,
                }
            )
        except IntegrityError as exc:
            # uq_protocol_instances_patient_number: two creates for the same
            # patient read the same next_instance_number and raced.
            raise ConflictError(
                "Another protocol instance was opened for this patient at the same moment — please retry",
                code="INSTANCE_NUMBER_CONFLICT",
            ) from exc

        await emit_event(
            self.session,
            aggregate_type="protocol_instance",
            aggregate_id=created["instance_id"],
            event_type="protocol_instance.created",
            payload={
                "instance_id": str(created["instance_id"]),
                "patient_id": str(patient_profile_id),
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

    async def _assert_prs_instance_patient_match(self, prs_instance_id: str, appt_patient_id) -> None:
        """instance_id here is prs_assessment_instances.instance_id (the
        actual PRS assessment being linked) — distinct from protocol_
        instances.instance_id despite the shared name. The only DB-side
        guard (fn_check_prs_appointment_type) checks the appointment's TYPE,
        not whose PRS assessment this is — without this, a caller could link
        one patient's completed PRS assessment to a different patient's
        device-session/follow-up appointment."""
        from app.modules.prs.repository import AssessmentInstanceRepository

        instance = await AssessmentInstanceRepository(self.session).get(prs_instance_id)
        if not instance:
            raise NotFoundError("PRS assessment instance not found", code="PRS_INSTANCE_NOT_FOUND")
        if str(instance["patient_id"]) != str(appt_patient_id):
            raise ValidationError("PRS assessment instance belongs to a different patient", code="PRS_INSTANCE_PATIENT_MISMATCH")

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
        await self._assert_prs_instance_patient_match(body.instance_id, appt["patient_id"])

        # Hard-reject a late submission against a superseded protocol — the
        # actual enforcement for the PRS-freeze mechanism. Without this, a
        # very late answer could still be recorded here and retroactively
        # change an already-reported protocol version's outcome numbers,
        # even after its scale rows were marked 'frozen'.
        from app.modules.device_sessions.repository import DeviceSessionRepository, DeviceSessionScaleRepository

        header = await DeviceSessionRepository(self.session).get_by_appointment(body.appointment_id)
        if header and await DeviceSessionScaleRepository(self.session).any_frozen_for_session(header["device_session_record_id"]):
            raise BusinessRuleError(
                "This assessment is no longer open — the protocol it belonged to has since been amended",
                code="SESSION_SCALE_FROZEN",
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

        await self._complete_due_scales(appt["appointment_id"], body.instance_id, body.scale_id)
        return {**row, "response_id": row["ds_prs_id"], "kind": "device_session"}

    async def _complete_due_scales(self, appointment_id: UUID, prs_instance_id: str, scale_id: str) -> None:
        """device_session_scales tracks per-scale delivery for this visit
        (seeded 'pending' by list_scales_due, device_sessions module) but
        nothing ever advanced it past that — this is the missing link,
        called once the PRS instance recorded above is confirmed to be this
        patient's. Matches by protocol_scales.prs_scale_id, the same
        catalogue-identity join _SESSION_SCALE_SELECT already relies on.

        Scoped to the single scale_id the caller says this session actually
        administered — NOT every prs_scale_results row on the instance.
        instance_id is disease-scoped (core.prs_assessment_instances has no
        per-scale column) and can carry results for sibling scales answered
        elsewhere under the same disease/patient; sweeping all of them here
        previously marked those unanswered siblings 'completed' too."""
        from app.modules.device_sessions.repository import DeviceSessionRepository, DeviceSessionScaleRepository

        header = await DeviceSessionRepository(self.session).get_by_appointment(appointment_id)
        if not header:
            return
        await DeviceSessionScaleRepository(self.session).complete_for_prs_instance(
            header["device_session_record_id"], prs_instance_id, [scale_id]
        )

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
        await self._assert_prs_instance_patient_match(body.instance_id, appt["patient_id"])
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
