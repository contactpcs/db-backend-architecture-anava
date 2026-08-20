"""HTTP surface for live tDCS device-treatment sessions.

Every endpoint hangs off /device-sessions/{appointment_id}/... — the
appointment is the natural key the CA app and patient app both already
navigate by, and device_sessions.appointment_id is UNIQUE (uq_device_sessions_appointment),
so it doubles as the session's own identity without a second id in the URL.

Role gates mirror treatment_protocols/router.py's group pattern: _ALL_STAFF
for read-mostly staff endpoints, a narrower _CA_WRITERS for the clinical
assistant actions that actually run the session, and _PATIENT_WRITERS for
the three places a patient (or a CA on the patient's behalf) files something
about their own experience — feedback, scale delivery status, raising SOS.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.device_sessions import schemas as s
from app.modules.device_sessions.service import DeviceSessionService

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
_CA_WRITERS = ("super_admin", "clinical_assistant")
_READERS = (*_ALL_STAFF, "patient")
_PATIENT_WRITERS = ("super_admin", "clinical_assistant", "patient")  # feedback, scale status, sos raise


# --------------------------------------------------------------------------
# Header / checklist
# --------------------------------------------------------------------------


@router.get("/device-sessions/{appointment_id}", response_model=s.DeviceSessionDetail)
async def get_device_session(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    """Full record — header + every hydrated child list. Powers the CA
    "return to session" resume view and the post-session summary screen."""
    return await DeviceSessionService(db).get_or_404(appointment_id, ctx)


@router.post("/device-sessions/{appointment_id}/checklist", response_model=s.DeviceSessionRead)
async def update_checklist(
    appointment_id: UUID,
    body: s.ChecklistUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """Upserts the pre-session safety checklist. Lazily creates the header
    row on the first write for this appointment."""
    return await DeviceSessionService(db).create_or_update_checklist(appointment_id, body, ctx)


# --------------------------------------------------------------------------
# Session-status FSM
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/start", response_model=s.DeviceSessionRead)
async def start_session(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """not_started/paused -> in_progress. Also moves the appointment to
    'in_progress' via the existing scheduling FSM."""
    return await DeviceSessionService(db).start(appointment_id, ctx)


@router.post("/device-sessions/{appointment_id}/pause", response_model=s.DeviceSessionRead)
async def pause_session(
    appointment_id: UUID,
    body: s.PauseStopRequest,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """in_progress -> paused. Does NOT touch appointments.status — see 53's
    file header for why pause/resume are session-local only."""
    return await DeviceSessionService(db).pause(appointment_id, body.pause_stop_reason, body.pause_stop_reason_detail, ctx)


@router.post("/device-sessions/{appointment_id}/resume", response_model=s.DeviceSessionRead)
async def resume_session(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """paused -> in_progress."""
    return await DeviceSessionService(db).resume(appointment_id, ctx)


@router.post("/device-sessions/{appointment_id}/stop", response_model=s.DeviceSessionRead)
async def stop_session(
    appointment_id: UUID,
    body: s.PauseStopRequest,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """in_progress/paused -> stopped_early (terminal). Marks the appointment
    completed — a stopped-early session still "happened"."""
    return await DeviceSessionService(db).stop(appointment_id, body.pause_stop_reason, body.pause_stop_reason_detail, ctx)


@router.post("/device-sessions/{appointment_id}/complete", response_model=s.DeviceSessionRead)
async def complete_session(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """in_progress -> completed. Marks the appointment completed."""
    return await DeviceSessionService(db).complete(appointment_id, ctx)


# --------------------------------------------------------------------------
# Device fit
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/device-fit", response_model=s.DeviceSessionRead)
async def set_device_fit(
    appointment_id: UUID,
    body: s.DeviceFitUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    return await DeviceSessionService(db).set_device_fit(appointment_id, body.device_fit_checklist, body.impedance_kohm, ctx)


# --------------------------------------------------------------------------
# Symptoms
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/symptoms", response_model=s.SymptomRead, status_code=201)
async def record_symptom(
    appointment_id: UUID,
    body: s.SymptomCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    return await DeviceSessionService(db).record_symptom(appointment_id, body, ctx)


@router.get("/device-sessions/{appointment_id}/symptoms", response_model=list[s.SymptomRead])
async def list_symptoms(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await DeviceSessionService(db).list_symptoms(appointment_id, ctx)


# --------------------------------------------------------------------------
# Adverse events
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/adverse-events", response_model=s.AdverseEventRead, status_code=201)
async def record_adverse_event(
    appointment_id: UUID,
    body: s.AdverseEventCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    return await DeviceSessionService(db).record_adverse_event(appointment_id, body, ctx)


@router.get("/device-sessions/{appointment_id}/adverse-events", response_model=list[s.AdverseEventRead])
async def list_adverse_events(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await DeviceSessionService(db).list_adverse_events(appointment_id, ctx)


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/notes", response_model=s.NoteRead, status_code=201)
async def add_note(
    appointment_id: UUID,
    body: s.NoteCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    return await DeviceSessionService(db).add_note(appointment_id, body, ctx)


@router.get("/device-sessions/{appointment_id}/notes", response_model=list[s.NoteRead])
async def list_notes(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await DeviceSessionService(db).list_notes(appointment_id, ctx)


# --------------------------------------------------------------------------
# Activities
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/activities", response_model=s.ActivityRead, status_code=201)
async def record_activity(
    appointment_id: UUID,
    body: s.ActivityCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    return await DeviceSessionService(db).record_activity(appointment_id, body, ctx)


@router.get("/device-sessions/{appointment_id}/activities", response_model=list[s.ActivityRead])
async def list_activities(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await DeviceSessionService(db).list_activities(appointment_id, ctx)


# --------------------------------------------------------------------------
# Scales
# --------------------------------------------------------------------------


@router.get("/device-sessions/{appointment_id}/scales", response_model=list[s.SessionScaleRead])
async def list_scales(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    """Seeds device_session_scales from the protocol's scale list on first
    read, then returns every scale due this visit."""
    return await DeviceSessionService(db).list_scales_due(appointment_id, ctx)


@router.patch("/device-sessions/{appointment_id}/scales/{protocol_scale_id}", response_model=s.SessionScaleRead)
async def set_scale_delivery(
    appointment_id: UUID,
    protocol_scale_id: UUID,
    body: s.ScaleDeliveryUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PATIENT_WRITERS)),
):
    """The CA-administered-vs-patient-app toggle."""
    return await DeviceSessionService(db).set_scale_delivery(appointment_id, protocol_scale_id, body.delivery_mode, ctx)


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/feedback", response_model=s.FeedbackRead, status_code=201)
async def record_feedback(
    appointment_id: UUID,
    body: s.FeedbackCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PATIENT_WRITERS)),
):
    """Patient, or CA on the patient's behalf. One row per session
    (uq_dsf_device_session) — a repeat call 409s."""
    return await DeviceSessionService(db).record_feedback(appointment_id, body.answers.model_dump(), body.quote, ctx)


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/media/consent", status_code=201)
async def confirm_media_consent(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """Records the consent-confirmation moment as an audit-trail event —
    the actual gate is chk_dsm_consent_required on device_session_media."""
    return await DeviceSessionService(db).confirm_media_consent(appointment_id, ctx)


@router.post("/device-sessions/{appointment_id}/media", response_model=s.MediaRead, status_code=201)
async def add_media(
    appointment_id: UUID,
    body: s.MediaCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """Refuses with 400 MEDIA_CONSENT_NOT_CONFIRMED unless /media/consent
    was already called for this session."""
    return await DeviceSessionService(db).add_media(appointment_id, body.media_type, body.file_key, ctx)


@router.get("/device-sessions/{appointment_id}/media", response_model=list[s.MediaRead])
async def list_media(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await DeviceSessionService(db).list_media(appointment_id, ctx)


# --------------------------------------------------------------------------
# Next session
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/next-session", response_model=s.DeviceSessionRead)
async def confirm_next_session(
    appointment_id: UUID,
    body: s.NextSessionConfirmation,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    """A soft confirmation of what the patient asked for — an actual
    reschedule still goes through the normal appointments booking path."""
    return await DeviceSessionService(db).confirm_next_session(appointment_id, body.model_dump(), ctx)


# --------------------------------------------------------------------------
# Events — the audit trail
# --------------------------------------------------------------------------


@router.get("/device-sessions/{appointment_id}/events", response_model=list[s.EventRead])
async def list_events(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    """The full timeline for the summary screen."""
    return await DeviceSessionService(db).list_events(appointment_id, ctx)


# --------------------------------------------------------------------------
# SOS
# --------------------------------------------------------------------------


@router.post("/device-sessions/{appointment_id}/sos", response_model=s.SosEventRead, status_code=201)
async def raise_sos(
    appointment_id: UUID,
    body: s.SosEventCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PATIENT_WRITERS)),
):
    """Role-gated to staff-or-patient at the router; the service layer
    further restricts this to the patient in the session themselves."""
    return await DeviceSessionService(db).raise_sos(appointment_id, body.sos_type, body.note, ctx)


@router.patch("/device-sessions/{appointment_id}/sos/{sos_id}/ack", response_model=s.SosEventRead)
async def acknowledge_sos(
    appointment_id: UUID,
    sos_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_CA_WRITERS)),
):
    return await DeviceSessionService(db).acknowledge_sos(appointment_id, sos_id, ctx)


@router.get("/device-sessions/{appointment_id}/sos", response_model=list[s.SosEventRead])
async def list_sos_events(
    appointment_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await DeviceSessionService(db).list_sos_events(appointment_id, ctx)
