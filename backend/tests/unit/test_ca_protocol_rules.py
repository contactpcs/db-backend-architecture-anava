"""Clinical-assistant protocol rules (SQL 106): a CA amends, never creates;
activates only their own amendment; the activity timeline hides lifecycle
steps and diffs from a CA. Mocks the DB so none is needed."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_
from app.modules.audit import service as audit_service
from app.modules.audit.service import ClinicalActivityService, prescription_changes
from app.modules.treatment_protocols import router as protocol_router
from app.modules.treatment_protocols.service import ProtocolService

CA = "00000000-0000-0000-0000-0000000000ca"
OTHER = "00000000-0000-0000-0000-0000000000d0"


def _ctx(role: str, user_id: str = CA) -> RequestContext:
    return RequestContext(user_id=user_id, role=role, clinic_id=str(uuid4()), region_id=None)


def _draft(**over) -> dict:
    return {"clinic_id": uuid4(), "status": "draft", "set_by": CA, "supersedes_protocol_id": uuid4(), **over}


@pytest.mark.asyncio
async def test_ca_cannot_create_a_new_protocol():
    body = MagicMock(supersedes_protocol_id=None)
    with pytest.raises(PermissionError_) as exc:
        await ProtocolService(AsyncMock()).create(body, _ctx("clinical_assistant"))
    assert exc.value.code == "CA_CANNOT_CREATE_PROTOCOL"


@pytest.mark.asyncio
async def test_ca_amendment_passes_the_create_guard():
    svc = ProtocolService(AsyncMock())
    reached = RuntimeError("past the guard")
    with patch.object(svc, "_resolve_parent", AsyncMock(side_effect=reached)):
        with pytest.raises(RuntimeError):
            await svc.create(MagicMock(supersedes_protocol_id=uuid4()), _ctx("clinical_assistant"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        _draft(supersedes_protocol_id=None),  # a doctor's first draft, even if somehow set_by the CA
        _draft(set_by=OTHER),  # someone else's amendment
    ],
)
async def test_ca_cannot_activate_what_is_not_their_own_amendment(row):
    svc = ProtocolService(AsyncMock())
    with patch.object(svc, "get_or_404", AsyncMock(return_value=row)):
        with pytest.raises(PermissionError_) as exc:
            await svc.activate(uuid4(), _ctx("clinical_assistant"))
    assert exc.value.code == "CA_CANNOT_ACTIVATE_PROTOCOL"


@pytest.mark.asyncio
async def test_ca_own_amendment_passes_the_activate_guard():
    svc = ProtocolService(AsyncMock())
    svc.repo.has_generated_appointments = AsyncMock(side_effect=RuntimeError("past the guard"))
    with patch.object(svc, "get_or_404", AsyncMock(return_value=_draft())):
        with pytest.raises(RuntimeError):
            await svc.activate(uuid4(), _ctx("clinical_assistant"))


def test_router_keeps_ca_out_of_create_instance_cancel_and_complete():
    assert "clinical_assistant" in protocol_router._AMENDERS
    assert "clinical_assistant" not in protocol_router._PRESCRIBERS


def test_prescription_changes_ignores_bookkeeping_columns():
    old = {"protocol_id": "a", "status": "superseded", "version_minor": 0, "prescribed_current_ma": 1.5, "session_count": 20}
    new = {"protocol_id": "b", "status": "active", "version_minor": 1, "prescribed_current_ma": 2.0, "session_count": 20}
    assert prescription_changes(old, new) == {"prescribed_current_ma": {"from": 1.5, "to": 2.0}}


@pytest.mark.asyncio
@pytest.mark.parametrize(("role", "sees_lifecycle"), [("doctor", True), ("clinical_assistant", False)])
async def test_timeline_lifecycle_rows_are_full_trail_only(role, sees_lifecycle):
    session = AsyncMock()
    session.execute.return_value = MagicMock(mappings=lambda: MagicMock(all=lambda: []))
    with patch.object(audit_service, "resolve_patient_profile_id", AsyncMock(return_value=uuid4())):
        assert await ClinicalActivityService(session).for_patient(uuid4(), _ctx(role)) == []
    sql = str(session.execute.call_args.args[0])
    assert ("activity_logs" in sql) is sees_lifecycle


@pytest.mark.parametrize("status", ["checked_in", "in_progress", "completed"])
def test_ca_can_check_in_start_and_complete_a_consultation(status):
    from app.modules.scheduling.service import _ALLOWED_FROM, AppointmentService

    appt = {"appointment_type": "initial", "status": next(iter(_ALLOWED_FROM[status])), "doctor_id": OTHER, "patient_id": uuid4()}
    AppointmentService(AsyncMock())._authorize_transition(appt, status=status, ctx=_ctx("clinical_assistant"))


def test_receptionist_still_cannot_start_a_consultation():
    from app.modules.scheduling.service import AppointmentService

    appt = {"appointment_type": "initial", "status": "checked_in", "doctor_id": OTHER, "patient_id": uuid4()}
    with pytest.raises(PermissionError_):
        AppointmentService(AsyncMock())._authorize_transition(appt, status="in_progress", ctx=_ctx("receptionist"))
