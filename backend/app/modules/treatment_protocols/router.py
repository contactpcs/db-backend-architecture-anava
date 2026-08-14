"""HTTP surface for the Treatment Protocol wizard.

Endpoints are grouped to match the 8 steps the UI walks:

  Step 1  GET  /neuromod/devices?clinic_id=...    (+ /device-companies)
          The prescribing UI must pass clinic_id — a doctor may only be
          offered devices their clinic owns (core.clinic_devices, 37).
  Step 2  GET  /neuromod/conditions
  Step 3  GET  /neuromod/diagnoses
          POST /neuromod/diagnoses/resolve
  Step 4  GET  /neuromod/placements
          POST /neuromod/placements/validate
  Step 5  GET  /neuromod/dosing
  Step 6  GET  /neuromod/scales
  Step 7  POST /treatment-protocols/schedule-preview
  Step 8  POST /treatment-protocols
          POST /treatment-protocols/{id}/activate

Catalogue reads are open to all staff and to the patient (the patient app
renders its own protocol summary). Writes are doctor-and-above: a protocol
is a prescription.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.treatment_protocols import schemas as s
from app.modules.treatment_protocols.service import (
    CatalogueService,
    ProtocolPrsService,
    ProtocolService,
    ScheduleService,
)

router = APIRouter()

_ALL_STAFF = ("super_admin", "regional_admin", "clinic_admin", "doctor", "clinical_assistant", "receptionist")
_READERS = (*_ALL_STAFF, "patient")
# Prescribing roles. A clinical assistant runs the device but does not set
# the protocol.
_PRESCRIBERS = ("super_admin", "clinic_admin", "doctor")
# Who may file a PRS score: the CA administering the session, the doctor,
# and the patient answering on their own device.
_PRS_WRITERS = ("super_admin", "clinic_admin", "doctor", "clinical_assistant", "patient")


# --------------------------------------------------------------------------
# Step 1 - Device
# --------------------------------------------------------------------------


@router.get("/neuromod/device-companies", response_model=list[s.DeviceCompanyRead])
async def list_device_companies(
    active_only: bool = Query(True),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await CatalogueService(db).list_companies(active_only=active_only)


@router.get("/neuromod/devices", response_model=list[s.DeviceRead])
async def list_devices(
    phase: int | None = Query(None, ge=1, le=2, description="1 = selectable now, 2 = catalogued only"),
    active_only: bool = Query(True),
    clinic_id: UUID | None = Query(
        None,
        description="Narrow to devices this clinic actually owns. The prescribing UI should ALWAYS send it.",
    ),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    """Step 1 — the device picker.

    Pass clinic_id when prescribing. Without it this returns the whole
    catalogue, which is correct for a superadmin curating the registry and
    wrong for a doctor: it would offer machines that are not in their building.

    With it, each row also carries clinic_quantity — how many units that clinic
    owns. That is NOT the same as clinic_device_schedules.capacity, which is how
    many sessions may run at once and also depends on assistants on shift.

    Filtering here is a convenience, not the guarantee:
    trg_check_device_available_at_clinic (37) independently refuses a protocol
    naming a device the clinic does not have, so a stale tab or a direct API
    call cannot slip past.
    """
    return await CatalogueService(db).list_devices(phase=phase, active_only=active_only, clinic_id=clinic_id)


@router.get("/neuromod/devices/{device_id}", response_model=s.DeviceRead)
async def get_device(device_id: UUID, db=Depends(get_db), _ctx: RequestContext = Depends(require_role(*_READERS))):
    return await CatalogueService(db).get_device_or_404(device_id)


# --------------------------------------------------------------------------
# Step 2 - Condition
# --------------------------------------------------------------------------


@router.get("/neuromod/conditions", response_model=list[s.ConditionRead])
async def list_conditions(
    device_id: UUID | None = Query(None, description="Scopes the evidence chip to this device's dosing rows"),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await CatalogueService(db).list_conditions(device_id=device_id)


# --------------------------------------------------------------------------
# Step 3 - Diagnosis
# --------------------------------------------------------------------------


@router.get("/neuromod/diagnoses", response_model=list[s.DiagnosisRead])
async def list_diagnoses(
    condition_id: list[UUID] | None = Query(None),
    device_id: UUID | None = Query(None, description="Hydrates the suggested montage column"),
    q: str | None = Query(None, description="Search ICD-10 code or description"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await CatalogueService(db).list_diagnoses(
        condition_ids=condition_id,
        query=q,
        device_id=device_id,
        skip=skip,
        limit=limit,
    )


@router.post("/neuromod/diagnoses/resolve", response_model=s.DiagnosisResolution)
async def resolve_diagnoses(
    device_id: UUID = Body(..., embed=True),
    diagnosis_ids: list[UUID] = Body(default_factory=list, embed=True),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    """Ranks the selected codes by evidence and returns the driving
    suggestion plus one-click alternates."""
    return await CatalogueService(db).resolve_diagnoses(diagnosis_ids=diagnosis_ids, device_id=device_id)


# --------------------------------------------------------------------------
# Step 4 - Placement
# --------------------------------------------------------------------------


@router.get("/neuromod/placements", response_model=list[s.PlacementRead])
async def list_placements(
    device_id: UUID = Query(...),
    condition_id: UUID | None = Query(None),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await CatalogueService(db).list_placements(device_id=device_id, condition_id=condition_id)


@router.post("/neuromod/placements/validate", response_model=s.ElectrodeValidationResult)
async def validate_placement(
    body: s.ElectrodeValidationRequest,
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    """Checks a custom montage against the device's electrode rule before
    the doctor leaves the step."""
    return await CatalogueService(db).validate_electrodes(body)


# --------------------------------------------------------------------------
# Step 5 - Dosing
# --------------------------------------------------------------------------


@router.get("/neuromod/dosing", response_model=list[s.DosingRead])
async def list_dosing(
    device_id: UUID = Query(...),
    condition_id: UUID | None = Query(None),
    placement_id: UUID | None = Query(None),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await CatalogueService(db).list_dosing(device_id=device_id, condition_id=condition_id, placement_id=placement_id)


# --------------------------------------------------------------------------
# Step 6 - Scales
# --------------------------------------------------------------------------


@router.get("/neuromod/scales", response_model=list[s.ScaleRead])
async def list_scales(
    condition_id: list[UUID] | None = Query(None),
    db=Depends(get_db),
    _ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await CatalogueService(db).list_scales(condition_ids=condition_id)


# --------------------------------------------------------------------------
# Step 7 - Schedule preview
# --------------------------------------------------------------------------


@router.post("/treatment-protocols/schedule-preview", response_model=s.SchedulePreview)
async def preview_schedule(
    body: s.SchedulePreviewRequest,
    _ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    """Pure date arithmetic - writes nothing, so the doctor can re-preview
    as often as they like."""
    return ScheduleService.build(body)


# --------------------------------------------------------------------------
# Step 8 - Protocol lifecycle
# --------------------------------------------------------------------------


@router.post("/treatment-protocols", response_model=s.ProtocolRead, status_code=201)
async def create_protocol(
    body: s.ProtocolCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRESCRIBERS)),
):
    """Creates the protocol as a draft and generates its whole course of
    appointments in the same transaction."""
    return await ProtocolService(db).create(body, ctx)


@router.get("/treatment-protocols", response_model=list[s.ProtocolRead])
async def list_protocols(
    plan_id: UUID | None = Query(None),
    patient_id: UUID | None = Query(None),
    status: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await ProtocolService(db).list(ctx, plan_id=plan_id, patient_id=patient_id, status=status, skip=skip, limit=limit)


@router.get("/treatment-protocols/{protocol_id}", response_model=s.ProtocolDetail)
async def get_protocol(
    protocol_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_ALL_STAFF)),
):
    return await ProtocolService(db).get_detail(protocol_id, ctx)


@router.patch("/treatment-protocols/{protocol_id}", response_model=s.ProtocolRead)
async def update_protocol(
    protocol_id: UUID,
    body: s.ProtocolUpdate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRESCRIBERS)),
):
    """Draft-only. An active protocol is amended by cancelling and
    re-issuing, not edited in place."""
    return await ProtocolService(db).update(protocol_id, body, ctx)


@router.get("/treatment-protocols/{protocol_id}/sessions", response_model=list[s.ProtocolSessionRead])
async def list_protocol_sessions(
    protocol_id: UUID,
    appointment_type: str | None = Query(None, description="device_session | protocol_followup"),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await ProtocolService(db).list_sessions(protocol_id, ctx, appointment_type=appointment_type)


@router.post("/treatment-protocols/{protocol_id}/activate", response_model=s.ProtocolRead)
async def activate_protocol(
    protocol_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRESCRIBERS)),
):
    """The 'Push Treatment Protocol' action. Makes the protocol live and
    visible to the care team."""
    return await ProtocolService(db).activate(protocol_id, ctx)


@router.post("/treatment-protocols/{protocol_id}/cancel", response_model=s.ProtocolRead)
async def cancel_protocol(
    protocol_id: UUID,
    reason: str = Body(..., embed=True, min_length=3),
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRESCRIBERS)),
):
    """Cancels the protocol and its not-yet-booked sessions. Sessions the
    patient has already claimed are left alone."""
    return await ProtocolService(db).cancel(protocol_id, ctx, reason=reason)


@router.post("/treatment-protocols/{protocol_id}/complete", response_model=s.ProtocolRead)
async def complete_protocol(
    protocol_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRESCRIBERS)),
):
    return await ProtocolService(db).complete(protocol_id, ctx)


# --------------------------------------------------------------------------
# PRS responses
# --------------------------------------------------------------------------


@router.post("/treatment-protocols/{protocol_id}/prs/device-session", response_model=s.PrsResponseRead, status_code=201)
async def record_device_session_prs(
    protocol_id: UUID,
    body: s.DeviceSessionPrsCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRS_WRITERS)),
):
    """PRS taken after a device session."""
    return await ProtocolPrsService(db).record_device_session(protocol_id, body, ctx)


@router.post("/treatment-protocols/{protocol_id}/prs/follow-up", response_model=s.PrsResponseRead, status_code=201)
async def record_follow_up_prs(
    protocol_id: UUID,
    body: s.FollowUpPrsCreate,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_PRS_WRITERS)),
):
    """PRS taken during a follow-up consultation. Stored separately from
    device-session responses by requirement."""
    return await ProtocolPrsService(db).record_follow_up(protocol_id, body, ctx)


@router.get("/treatment-protocols/{protocol_id}/prs", response_model=list[s.PrsResponseRead])
async def list_protocol_prs(
    protocol_id: UUID,
    db=Depends(get_db),
    ctx: RequestContext = Depends(require_role(*_READERS)),
):
    return await ProtocolPrsService(db).list_for_protocol(protocol_id, ctx)
