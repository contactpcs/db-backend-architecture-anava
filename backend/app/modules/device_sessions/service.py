"""Business rules for the Device Session module.

The live-session FSM lives here, not in the schema: device_sessions.session_status
carries a CHECK on its five values (chk_device_sessions_status) but Postgres has
no per-transition guard, so start/pause/resume/stop/complete each validate their
own from-state before writing, the same way ProtocolService's own lifecycle
methods do.

Two coarse appointment-level transitions ride on top of this session-level FSM
— in_progress on start, completed on complete-or-stop-early — and BOTH of them
go through the EXISTING scheduling.AppointmentRepository.update_status rather
than a second write path onto core.appointments. That is not a style choice:
appointments.status is shared by every appointment type and tightly
role-guarded (scheduling/service.py's own _ALLOWED_FROM), and 53's file header
is explicit that this feature must not add a 'paused' value to it. Pause and
resume therefore touch ONLY device_sessions.session_status and never call
update_status at all.
"""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionError_, ValidationError
from app.core.fsm import assert_transition
from app.core.scoping import assert_clinic_scope
from app.modules.device_sessions.repository import (
    DeviceSessionActivityRepository,
    DeviceSessionAdverseEventRepository,
    DeviceSessionEventRepository,
    DeviceSessionFeedbackRepository,
    DeviceSessionMediaRepository,
    DeviceSessionNoteRepository,
    DeviceSessionRepository,
    DeviceSessionScaleRepository,
    DeviceSessionSosEventRepository,
    DeviceSessionSymptomRepository,
)
from app.modules.scheduling.repository import AppointmentRepository

_TYPE_DEVICE_SESSION = "device_session"

# Session-level FSM. Deliberately separate from appointments.status — see
# module docstring and 53's file header "WHAT THIS FILE DELIBERATELY DOES NOT DO".
#
# Keyed by CURRENT state -> allowed NEW states, matching app.core.fsm.
# assert_transition's actual contract (`new not in transitions.get(current)`).
# Originally written keyed by destination -> allowed sources (the inverse),
# which made every single call raise unconditionally — not_started has no
# entry as a KEY in that shape, so transitions.get("not_started") always
# returned {}  and start() 400'd on every first session, always.
_TRANSITIONS: dict[str, set[str]] = {
    "not_started": {"in_progress"},
    "in_progress": {"paused", "completed", "stopped_early"},
    "paused": {"in_progress", "stopped_early"},
}

_DEFAULT_DELIVERY_MODE = "ca_administered"


class DeviceSessionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DeviceSessionRepository(session)
        self.symptoms = DeviceSessionSymptomRepository(session)
        self.adverse_events = DeviceSessionAdverseEventRepository(session)
        self.notes = DeviceSessionNoteRepository(session)
        self.activities = DeviceSessionActivityRepository(session)
        self.scales = DeviceSessionScaleRepository(session)
        self.feedback = DeviceSessionFeedbackRepository(session)
        self.media = DeviceSessionMediaRepository(session)
        self.events = DeviceSessionEventRepository(session)
        self.sos_events = DeviceSessionSosEventRepository(session)
        self.appointments = AppointmentRepository(session)

    # -- appointment / header resolution ------------------------------------

    async def _appointment_or_404(self, appointment_id: UUID) -> dict:
        appt = await self.appointments.get(appointment_id)
        if not appt:
            raise NotFoundError("Appointment not found", code="APPOINTMENT_NOT_FOUND")
        return appt

    async def _assert_device_session_appointment(self, appt: dict) -> None:
        if appt["appointment_type"] != _TYPE_DEVICE_SESSION:
            raise ValidationError(
                f"Appointment is a '{appt['appointment_type']}', not a device session",
                code="WRONG_APPOINTMENT_TYPE",
            )

    async def _header_or_404(self, appointment_id: UUID) -> dict:
        header = await self.repo.get_by_appointment(appointment_id)
        if not header:
            raise NotFoundError(
                "No device session record yet for this appointment — file the pre-session checklist first",
                code="DEVICE_SESSION_NOT_FOUND",
            )
        return header

    async def _resolve_scoped_appointment(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        appt = await self._appointment_or_404(appointment_id)
        await self._assert_device_session_appointment(appt)
        await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        return appt

    # -- reads ----------------------------------------------------------------

    async def get_or_404(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        """Full record — header + hydrated child lists. Powers the CA
        "return to session" resume view and the post-session summary screen."""
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        sid = header["device_session_record_id"]
        detail = dict(header)
        detail["symptoms"] = await self.symptoms.list_for_session(sid)
        detail["adverse_events"] = await self.adverse_events.list_for_session(sid)
        detail["notes"] = await self.notes.list_for_session(sid)
        detail["activities"] = await self.activities.list_for_session(sid)
        detail["scales"] = await self.scales.list_for_session(sid)
        detail["feedback"] = await self.feedback.get_for_session(sid)
        detail["media"] = await self.media.list_for_session(sid)
        detail["events"] = await self.events.list_for_session(sid)
        detail["sos_events"] = await self.sos_events.list_for_session(sid)
        return detail

    # -- checklist / lazy header creation --------------------------------------

    async def create_or_update_checklist(self, appointment_id: UUID, body, ctx: RequestContext) -> dict:
        """Upserts the pre-session checklist fields onto the header row,
        creating it on first write. protocol_id is denormalised from the
        appointment (53's own comment on device_sessions.protocol_id), and
        created_by is stamped once at creation, never overwritten."""
        appt = await self._resolve_scoped_appointment(appointment_id, ctx)

        header = await self.repo.get_by_appointment(appointment_id)
        fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}

        if header is None:
            if not appt.get("protocol_id"):
                raise ValidationError(
                    "Appointment has no protocol_id — a device session cannot be checklisted without one",
                    code="APPOINTMENT_NO_PROTOCOL",
                )
            payload: dict[str, Any] = {
                "appointment_id": str(appointment_id),
                "protocol_id": str(appt["protocol_id"]),
                "created_by": ctx.user_id,
                **fields,
            }
            try:
                header = await self.repo.create(payload)
            except IntegrityError as exc:
                raise ConflictError("A device session record already exists for this appointment", code="DEVICE_SESSION_EXISTS") from exc
            return header

        if not fields:
            return header
        updated = await self.repo.update(header["device_session_record_id"], fields)
        return updated or header

    # -- session-status FSM -----------------------------------------------------

    async def _write_event(
        self, device_session_record_id: UUID, *, event_type: str, ctx: RequestContext, payload: dict | None = None
    ) -> None:
        await self.events.create(
            {
                "device_session_record_id": str(device_session_record_id),
                "event_type": event_type,
                "payload": payload or {},
                "actor_id": ctx.user_id,
                "actor_role": ctx.role,
            }
        )

    async def start(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        assert_transition(header["session_status"], "in_progress", _TRANSITIONS, entity="device session", code="INVALID_SESSION_TRANSITION")

        now_columns = ["started_at"] if header.get("started_at") is None else []
        updated = await self.repo.update_with_now_columns(
            header["device_session_record_id"], {"session_status": "in_progress"}, now_columns=now_columns
        )

        # Reuses the existing appointments FSM — appointments.status is the
        # single source of truth for this coarse transition, per the explicit
        # design decision in 53's file header.
        await self.appointments.update_status(appointment_id, status="in_progress", ca_id=ctx.user_id)

        await self._write_event(header["device_session_record_id"], event_type="started", ctx=ctx)
        await emit_event(
            self.session,
            aggregate_type="device_session",
            aggregate_id=header["device_session_record_id"],
            event_type="device_session.started",
            payload={"appointment_id": str(appointment_id), "device_session_record_id": str(header["device_session_record_id"])},
        )
        return updated or header

    async def pause(self, appointment_id: UUID, reason: str, detail: str | None, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        assert_transition(header["session_status"], "paused", _TRANSITIONS, entity="device session", code="INVALID_SESSION_TRANSITION")

        # Deliberately does NOT touch appointments.status — pause/resume live
        # entirely in device_sessions.session_status (53 file header).
        updated = await self.repo.update_with_now_columns(
            header["device_session_record_id"],
            {
                "session_status": "paused",
                "pause_stop_reason": reason,
                "pause_stop_reason_detail": detail,
            },
            now_columns=["paused_at"],
        )
        await self._write_event(header["device_session_record_id"], event_type="paused", ctx=ctx, payload={"reason": reason})
        return updated or header

    async def resume(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        assert_transition(header["session_status"], "in_progress", _TRANSITIONS, entity="device session", code="INVALID_SESSION_TRANSITION")

        updated = await self.repo.update_with_now_columns(
            header["device_session_record_id"], {"session_status": "in_progress"}, now_columns=["resumed_at"]
        )
        await self._write_event(header["device_session_record_id"], event_type="resumed", ctx=ctx)
        return updated or header

    async def stop(self, appointment_id: UUID, reason: str, detail: str | None, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        assert_transition(
            header["session_status"], "stopped_early", _TRANSITIONS, entity="device session", code="INVALID_SESSION_TRANSITION"
        )

        updated = await self.repo.update_with_now_columns(
            header["device_session_record_id"],
            {
                "session_status": "stopped_early",
                "pause_stop_reason": reason,
                "pause_stop_reason_detail": detail,
            },
            now_columns=["stopped_at"],
        )

        # A stopped-early session still "happened" per the plan — the
        # appointment is marked completed, not cancelled.
        await self.appointments.update_status(appointment_id, status="completed")

        await self._write_event(header["device_session_record_id"], event_type="stopped", ctx=ctx, payload={"reason": reason})
        await emit_event(
            self.session,
            aggregate_type="device_session",
            aggregate_id=header["device_session_record_id"],
            event_type="device_session.stopped",
            payload={"appointment_id": str(appointment_id), "reason": reason},
        )
        return updated or header

    async def complete(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        # Same light-touch pre-flight as ProtocolService.complete(): the
        # session must currently be in_progress. No further field-completeness
        # check is enforced here — that would over-engineer a guard the CA
        # workflow already walks the user through screen by screen.
        assert_transition(header["session_status"], "completed", _TRANSITIONS, entity="device session", code="INVALID_SESSION_TRANSITION")

        updated = await self.repo.update_with_now_columns(
            header["device_session_record_id"], {"session_status": "completed"}, now_columns=["completed_at"]
        )

        await self.appointments.update_status(appointment_id, status="completed")

        await self._write_event(header["device_session_record_id"], event_type="completed", ctx=ctx)
        await emit_event(
            self.session,
            aggregate_type="device_session",
            aggregate_id=header["device_session_record_id"],
            event_type="device_session.completed",
            payload={"appointment_id": str(appointment_id)},
        )
        return updated or header

    # -- device fit -------------------------------------------------------------

    async def set_device_fit(self, appointment_id: UUID, checklist: dict, impedance_kohm, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        fields: dict[str, Any] = {"device_fit_checklist": checklist}
        if impedance_kohm is not None:
            fields["impedance_kohm"] = impedance_kohm
        updated = await self.repo.update(header["device_session_record_id"], fields)
        return updated or header

    # -- append-only logs ---------------------------------------------------

    async def record_symptom(self, appointment_id: UUID, body, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        created = await self.symptoms.create(
            {
                "device_session_record_id": str(header["device_session_record_id"]),
                "symptom": body.symptom,
                "severity": body.severity,
                "note": body.note,
                "recorded_by": ctx.user_id,
            }
        )
        await self._write_event(
            header["device_session_record_id"],
            event_type="symptom_recorded",
            ctx=ctx,
            payload={"symptom": body.symptom, "severity": body.severity},
        )
        return created

    async def list_symptoms(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.symptoms.list_for_session(header["device_session_record_id"])

    async def record_adverse_event(self, appointment_id: UUID, body, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        created = await self.adverse_events.create(
            {
                "device_session_record_id": str(header["device_session_record_id"]),
                "event_type": body.event_type,
                "severity": body.severity,
                "description": body.description,
                "action_taken": body.action_taken,
                "recorded_by": ctx.user_id,
            }
        )
        await self._write_event(
            header["device_session_record_id"],
            event_type="ae_recorded",
            ctx=ctx,
            payload={"event_type": body.event_type, "severity": body.severity},
        )
        return created

    async def list_adverse_events(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.adverse_events.list_for_session(header["device_session_record_id"])

    async def add_note(self, appointment_id: UUID, body, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        created = await self.notes.create(
            {
                "device_session_record_id": str(header["device_session_record_id"]),
                "note_text": body.note_text,
                "recorded_by": ctx.user_id,
            }
        )
        await self._write_event(header["device_session_record_id"], event_type="note_added", ctx=ctx)
        return created

    async def list_notes(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.notes.list_for_session(header["device_session_record_id"])

    async def record_activity(self, appointment_id: UUID, body, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        created = await self.activities.create(
            {
                "device_session_record_id": str(header["device_session_record_id"]),
                "activities": body.activities,
                "free_text": body.free_text,
                "note": body.note,
                "recorded_by": ctx.user_id,
            }
        )
        await self._write_event(header["device_session_record_id"], event_type="activity_recorded", ctx=ctx)
        return created

    async def list_activities(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.activities.list_for_session(header["device_session_record_id"])

    # -- scales -------------------------------------------------------------

    async def list_scales_due(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        """Seeds device_session_scales from the protocol's protocol_scales on
        first read, so the CA screen always shows every scale due this visit
        even before any delivery-mode decision has been made."""
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        sid = header["device_session_record_id"]

        existing = await self.scales.list_for_session(sid)
        seeded_ids = {str(r["protocol_scale_id"]) for r in existing}

        protocol_scales = await self.scales.list_protocol_scales(header["protocol_id"])
        for ps in protocol_scales:
            if str(ps["protocol_scale_id"]) in seeded_ids:
                continue
            await self.scales.upsert(
                sid,
                ps["protocol_scale_id"],
                {"delivery_mode": _DEFAULT_DELIVERY_MODE, "status": "pending"},
            )

        return await self.scales.list_for_session(sid)

    async def set_scale_delivery(self, appointment_id: UUID, protocol_scale_id: UUID, delivery_mode: str, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment_for_patient_write(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        existing = await self.scales.get(header["device_session_record_id"], protocol_scale_id)
        if not existing:
            raise NotFoundError("No scale row for this protocol_scale_id on this session", code="SESSION_SCALE_NOT_FOUND")
        return await self.scales.upsert(
            header["device_session_record_id"],
            protocol_scale_id,
            {"delivery_mode": delivery_mode, "status": existing["status"]},
        )

    # -- feedback -------------------------------------------------------------

    async def record_feedback(self, appointment_id: UUID, answers: dict, quote: str | None, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment_for_patient_write(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        try:
            created = await self.feedback.create(
                {
                    "device_session_record_id": str(header["device_session_record_id"]),
                    "answers": answers,
                    "quote": quote,
                    "recorded_by": ctx.user_id,
                }
            )
        except IntegrityError as exc:
            # uq_dsf_device_session — one feedback row per session.
            raise ConflictError("Feedback has already been recorded for this session", code="FEEDBACK_ALREADY_RECORDED") from exc
        return created

    # -- media / consent ------------------------------------------------------

    async def confirm_media_consent(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        """Records the consent-confirmation moment as an event. There is no
        separate consent-storage column on the header by design (53's
        comment on chk_dsm_consent_required) — the CHECK on device_session_media
        is the actual gate; this just gives add_media something to point to."""
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        await self._write_event(header["device_session_record_id"], event_type="media_consent_confirmed", ctx=ctx)
        return {"device_session_record_id": header["device_session_record_id"], "consent_confirmed": True}

    async def add_media(self, appointment_id: UUID, media_type: str, file_key: str, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        sid = header["device_session_record_id"]

        consent_events = await self.events.list_for_session(sid)
        if not any(e["event_type"] == "media_consent_confirmed" for e in consent_events):
            raise BusinessRuleError(
                "Recording consent has not been confirmed for this session yet",
                code="MEDIA_CONSENT_NOT_CONFIRMED",
            )

        created = await self.media.create(
            {
                "device_session_record_id": str(sid),
                "recording_consent_confirmed": True,
                "media_type": media_type,
                "file_key": file_key,
                "uploaded_by": ctx.user_id,
            }
        )
        return created

    async def list_media(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.media.list_for_session(header["device_session_record_id"])

    # -- next session -----------------------------------------------------------

    async def confirm_next_session(self, appointment_id: UUID, confirmation: dict, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        updated = await self.repo.update(header["device_session_record_id"], {"next_session_confirmation": confirmation})
        return updated or header

    # -- events (read) ----------------------------------------------------------

    async def list_events(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.events.list_for_session(header["device_session_record_id"])

    # -- SOS ----------------------------------------------------------------

    async def _resolve_scoped_appointment_for_patient_write(self, appointment_id: UUID, ctx: RequestContext) -> dict:
        """Like _resolve_scoped_appointment, but also allows a patient writing
        their own session (feedback, scale status, SOS raise) — assert_clinic_scope
        alone would reject a patient ctx, which has no clinic_id to match."""
        appt = await self._appointment_or_404(appointment_id)
        await self._assert_device_session_appointment(appt)
        if ctx.role == "patient":
            if str(appt["patient_id"]) != ctx.user_id:
                raise PermissionError_("You can only access your own appointment", code="PATIENT_SCOPE_MISMATCH")
            return appt
        await assert_clinic_scope(ctx, self.session, appt["clinic_id"])
        return appt

    async def raise_sos(self, appointment_id: UUID, sos_type: str, note: str | None, ctx: RequestContext) -> dict:
        """Patient-only — the SOS button belongs to the patient experiencing
        the session, never a staff member raising it on their behalf."""
        appt = await self._appointment_or_404(appointment_id)
        await self._assert_device_session_appointment(appt)
        if ctx.role != "patient" or str(appt["patient_id"]) != ctx.user_id:
            raise PermissionError_("Only the patient in this session may raise an SOS", code="SOS_PATIENT_ONLY")
        header = await self._header_or_404(appointment_id)
        created = await self.sos_events.create(
            {
                "device_session_record_id": str(header["device_session_record_id"]),
                "sos_type": sos_type,
                "note": note,
                "raised_by": ctx.user_id,
            }
        )
        await self._write_event(
            header["device_session_record_id"],
            event_type="sos_raised",
            ctx=ctx,
            payload={"sos_type": sos_type, "sos_id": str(created["sos_id"])},
        )
        return created

    async def acknowledge_sos(self, appointment_id: UUID, sos_id: UUID, ctx: RequestContext) -> dict:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        sos = await self.sos_events.get(sos_id)
        if not sos or str(sos["device_session_record_id"]) != str(header["device_session_record_id"]):
            raise NotFoundError("SOS event not found for this session", code="SOS_EVENT_NOT_FOUND")
        if sos["acknowledged_at"] is not None:
            raise ConflictError("SOS event has already been acknowledged", code="SOS_ALREADY_ACKNOWLEDGED")
        updated = await self.sos_events.acknowledge(sos_id, acknowledged_by=UUID(ctx.user_id))
        await self._write_event(
            header["device_session_record_id"],
            event_type="sos_acknowledged",
            ctx=ctx,
            payload={"sos_id": str(sos_id)},
        )
        return updated or sos

    async def list_sos_events(self, appointment_id: UUID, ctx: RequestContext) -> builtins.list[dict]:
        await self._resolve_scoped_appointment(appointment_id, ctx)
        header = await self._header_or_404(appointment_id)
        return await self.sos_events.list_for_session(header["device_session_record_id"])
