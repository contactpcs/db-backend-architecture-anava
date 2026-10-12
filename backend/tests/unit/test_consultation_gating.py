"""Staff (doctor / clinical assistant) take a consultation anamnesis only
inside a started consultation. A PRS is not gated: staff take one at any
time, with or without an appointment. No DB needed."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import BusinessRuleError
from app.core.resolve import attach_actor
from app.modules.anamnesis import service as anamnesis_module
from app.modules.anamnesis.service import AnamnesisService
from app.modules.prs import service as prs_module
from app.modules.prs.service import PrsAssessmentService

PATIENT = uuid4()
PAST_THE_GATE = RuntimeError("past the gate")


def _session() -> AsyncMock:
    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=AsyncMock())  # the create() savepoint
    return session


def _prs(appt: dict | None):
    """A PRS service whose only reachable DB call after the gate is create()."""
    svc = PrsAssessmentService(_session())
    svc.instances = AsyncMock()
    svc.instances.find_for_appointment.return_value = None
    svc.instances.find_in_progress.return_value = None
    svc.instances.find_completed_standalone.return_value = None
    svc.instances.create.side_effect = PAST_THE_GATE
    repo = AsyncMock()
    repo.get.return_value = {"patient_id": PATIENT, **appt} if appt else None
    return svc, patch.object(prs_module, "AppointmentRepository", return_value=repo)


async def _start_prs(appt, *, initiated_by="doctor_on_behalf", stage="main_clinical", with_appointment=True):
    svc, appt_patch = _prs(appt)
    with appt_patch, patch.object(prs_module, "_resolve_profile_id", AsyncMock(return_value=PATIENT)):
        await svc.start(
            patient_id=uuid4(),
            disease_id="D1" if stage != "general_registration" else None,
            assessment_stage=stage,
            session_id=None,
            initiated_by=initiated_by,
            appointment_id=uuid4() if with_appointment else None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("appt", "kwargs"),
    [
        (None, {"with_appointment": False}),  # staff, no consultation at all
        ({"appointment_type": "initial", "status": "paid"}, {}),  # staff, consultation not started
        ({"appointment_type": "initial", "status": "checked_in"}, {}),
        ({"appointment_type": "initial", "status": "completed"}, {}),  # staff, after the consultation
        ({"appointment_type": "follow_up", "status": "in_progress"}, {}),  # started consultation
        ({"appointment_type": "device_session", "status": "completed"}, {}),  # device-session scale, after the session
        (None, {"initiated_by": "patient", "with_appointment": False}),  # patient's own PRS
        (None, {"stage": "general_registration", "with_appointment": False}),  # registration intake
    ],
)
async def test_prs_is_never_gated_on_a_consultation(appt, kwargs):
    with pytest.raises(RuntimeError):
        await _start_prs(appt, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(("by_staff", "blocked"), [(True, True), (False, False)])
async def test_consultation_anamnesis_needs_a_started_consultation_for_staff(by_staff, blocked):
    svc = AnamnesisService(_session())
    svc.assessments = AsyncMock()
    svc.assessments.get_by_appointment.return_value = None
    svc.assessments.create.side_effect = PAST_THE_GATE
    repo = AsyncMock()
    repo.get.return_value = {"patient_id": PATIENT, "appointment_type": "initial", "status": "checked_in"}
    with (
        patch.object(anamnesis_module, "AppointmentRepository", return_value=repo),
        patch.object(anamnesis_module, "_resolve_profile_id", AsyncMock(return_value=PATIENT)),
    ):
        with pytest.raises(BusinessRuleError if blocked else RuntimeError):
            await svc.start(
                uuid4(),
                submitted_by=uuid4(),
                taken_by="doctor_on_behalf",
                assessment_stage="main",
                appointment_id=uuid4(),
                by_staff=by_staff,
            )


@pytest.mark.asyncio
async def test_attach_actor_leaves_rows_without_an_actor_alone():
    rows = await attach_actor(AsyncMock(), [{"instance_id": "x", "administered_by": None}], "administered_by")
    assert rows == [{"instance_id": "x", "administered_by": None, "administered_by_name": None, "administered_by_role": None}]
