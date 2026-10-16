"""POST /treatment-protocols/{id}/amend — modify an active protocol in place
of re-prescribing it. Mocks the DB so none is needed."""

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import BusinessRuleError, ValidationError
from app.modules.treatment_protocols import schemas as s
from app.modules.treatment_protocols.service import ProtocolService

DOCTOR = "00000000-0000-0000-0000-0000000000d0"


def _ctx() -> RequestContext:
    return RequestContext(user_id=DOCTOR, role="doctor", clinic_id=str(uuid4()), region_id=None)


def _target(**over) -> dict:
    return {
        "protocol_id": uuid4(),
        "instance_id": uuid4(),
        "clinic_id": uuid4(),
        "device_id": uuid4(),
        "status": "active",
        "modality": "tDCS",
        "version_major": 1,
        "version_minor": 0,
        **over,
    }


def _raw(target: dict, **over) -> dict:
    return {
        "protocol_id": target["protocol_id"],
        "instance_id": target["instance_id"],
        "device_id": target["device_id"],
        "set_by": "someone-else",
        "tdcs_placement_id": uuid4(),
        "tdcs_dosing_id": uuid4(),
        "session_count": 20,
        "sessions_per_week": 5,
        "status": "active",
        "prescribed_current_ma": Decimal("1.5"),
        "prescribed_duration_min": 20,
        "ramp_seconds": 30,
        "prescribed_tvns_pattern": None,
        "prescribed_tvns_ramp_up_sec": None,
        "prescribed_tvns_ramp_down_sec": None,
        "supersedes_protocol_id": None,
        "version_major": 1,
        "version_minor": 0,
        "notes": "original",
        "activated_at": "2026-10-01",
        "created_at": "2026-10-01",
        "updated_at": "2026-10-01",
        **over,
    }


def _svc(target: dict, raw: dict, relinked: list[dict] | None = None) -> ProtocolService:
    svc = ProtocolService(AsyncMock())
    svc.repo.get_plan_row = AsyncMock(return_value=raw)
    svc.repo.create = AsyncMock(return_value={"protocol_id": "new-id"})
    svc.repo.set_status = AsyncMock()
    svc.details.list_scales = AsyncMock(return_value=[{"prs_scale_id": "GAD-7/2026", "scale_id": None, "cadence": "weekly"}])
    svc.details.list_diagnoses = AsyncMock(return_value=[])
    svc.details.copy_from = AsyncMock()
    svc.details.add_scales = AsyncMock()
    svc.sessions.relink_unstarted = AsyncMock(
        return_value=relinked if relinked is not None else [{"appointment_id": "a1", "appointment_type": "device_session"}]
    )
    svc.device_sessions.relink_protocol = AsyncMock()
    svc.followups.relink_protocol = AsyncMock()
    svc.sessions.cancel_planned = AsyncMock()
    return svc


def _body(**fields) -> s.ProtocolAmend:
    return s.ProtocolAmend(reason="Patient tolerance", clinical_note="Tingling at 1.5 mA", **fields)


async def _amend(svc: ProtocolService, target: dict, body: s.ProtocolAmend):
    with (
        patch.object(svc, "get_or_404", AsyncMock(return_value=target)),
        patch.object(svc, "activate", AsyncMock(return_value={"protocol_id": "new-id", "status": "active"})) as activate,
        patch("app.modules.treatment_protocols.service.assert_clinic_scope", AsyncMock()),
        patch("app.modules.treatment_protocols.service.emit_event", AsyncMock()),
        patch("app.modules.device_sessions.repository.DeviceSessionScaleRepository.freeze_pending_for_protocol", AsyncMock()),
    ):
        result = await svc.amend(target["protocol_id"], body, _ctx())
    return result, activate


@pytest.mark.asyncio
async def test_dose_change_creates_next_minor_version_and_moves_remaining_sessions():
    target = _target()
    svc = _svc(target, _raw(target))
    result, activate = await _amend(svc, target, _body(prescribed_current_ma=Decimal("1.0")))

    payload = svc.repo.create.call_args.args[0]
    assert payload["prescribed_current_ma"] == Decimal("1.0")
    # Everything not sent is carried over unchanged.
    assert payload["prescribed_duration_min"] == 20
    assert payload["session_count"] == 20
    assert payload["tdcs_placement_id"] == svc.repo.get_plan_row.return_value["tdcs_placement_id"]
    assert payload["version_major"] == 1 and payload["version_minor"] == 1
    assert payload["supersedes_protocol_id"] == str(target["protocol_id"])
    assert payload["status"] == "draft" and payload["set_by"] == DOCTOR
    assert payload["notes"].startswith("Reason: Patient tolerance")
    for lifecycle in ("protocol_id", "activated_at", "created_at", "updated_at"):
        assert lifecycle not in payload

    svc.details.copy_from.assert_awaited_once_with(
        source_id=target["protocol_id"], target_id="new-id", conditions=True, scales=True, diagnoses=True
    )
    svc.sessions.relink_unstarted.assert_awaited_once_with(target["protocol_id"], "new-id")
    svc.device_sessions.relink_protocol.assert_awaited_once_with(["a1"], "new-id")
    svc.repo.set_status.assert_awaited_once_with(target["protocol_id"], "superseded")
    activate.assert_awaited_once()
    assert result["status"] == "active"


@pytest.mark.asyncio
async def test_scales_sent_replace_the_list():
    target = _target()
    svc = _svc(target, _raw(target))
    svc._assert_scales_exist = AsyncMock()
    await _amend(svc, target, _body(scales=[s.ProtocolScaleAssignment(scale_id="PHQ-9/2026", cadence="Weekly")]))
    assert svc.details.copy_from.call_args.kwargs["scales"] is False
    svc.details.add_scales.assert_awaited_once()


@pytest.mark.asyncio
async def test_only_an_active_protocol_can_be_modified():
    target = _target(status="draft")
    with pytest.raises(BusinessRuleError) as exc:
        await _amend(_svc(target, _raw(target)), target, _body(prescribed_current_ma=Decimal("1.0")))
    assert exc.value.code == "PROTOCOL_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_an_amendment_must_change_something():
    target = _target()
    with pytest.raises(ValidationError) as exc:
        await _amend(_svc(target, _raw(target)), target, _body(prescribed_current_ma=Decimal("1.5")))
    assert exc.value.code == "AMENDMENT_NO_CHANGES"


@pytest.mark.asyncio
async def test_tvns_fields_are_refused_on_a_tdcs_protocol():
    target = _target()
    with pytest.raises(ValidationError) as exc:
        await _amend(_svc(target, _raw(target)), target, _body(prescribed_tvns_strength_pct=20))
    assert exc.value.code == "DOSE_FIELDS_MISMATCH"


@pytest.mark.asyncio
async def test_moving_tvns_off_intermittent_drops_its_ramps():
    target = _target(modality="tVNS")
    raw = _raw(target, prescribed_tvns_pattern="intermittent", prescribed_tvns_ramp_up_sec=5, prescribed_tvns_ramp_down_sec=5)
    svc = _svc(target, raw)
    await _amend(svc, target, _body(prescribed_tvns_pattern="continuous"))
    payload = svc.repo.create.call_args.args[0]
    assert payload["prescribed_tvns_ramp_up_sec"] is None and payload["prescribed_tvns_ramp_down_sec"] is None


@pytest.mark.asyncio
async def test_a_course_with_nothing_left_cannot_be_modified():
    target = _target()
    with pytest.raises(BusinessRuleError) as exc:
        await _amend(_svc(target, _raw(target), relinked=[]), target, _body(prescribed_current_ma=Decimal("1.0")))
    assert exc.value.code == "PROTOCOL_NOTHING_REMAINING"


def _replan_svc(target: dict, raw: dict, *, delivered=8, last_number=8, booked=None) -> ProtocolService:
    svc = _svc(target, raw)
    svc.sessions.lineage_progress = AsyncMock(return_value={"delivered": delivered, "last_number": last_number})
    svc.sessions.list_booked = AsyncMock(return_value=booked or [])
    svc.sessions.move_to_protocol = AsyncMock()
    svc.device_sessions.move_to_protocol = AsyncMock()
    svc._resolve_parent = AsyncMock(
        return_value={"instance_id": target["instance_id"], "patient_id": "p", "doctor_id": "d", "clinic_id": "c"}
    )
    svc._generate_appointments = AsyncMock(
        side_effect=lambda pid, parent, preview, **kw: {
            "sessions_created": len(preview["sessions"]),
            "follow_ups_created": len(preview["follow_ups"]),
        }
    )
    return svc


@pytest.mark.asyncio
async def test_schedule_change_replans_only_the_rest_of_the_course():
    target = _target()
    monday = dt.date.today() + dt.timedelta(days=(7 - dt.date.today().weekday()))
    booked_day = monday + dt.timedelta(days=1)
    booked = [{"appointment_id": "b1", "appointment_type": "device_session", "appointment_date": booked_day, "session_number": 12}]
    svc = _replan_svc(target, _raw(target, follow_up_every_n=None), booked=booked)

    await _amend(svc, target, _body(sessions_per_week=3, start_date=monday))

    # 20-session course, 8 delivered, 1 booked -> 11 new sessions, numbered
    # 9.. in date order with the booked one keeping its day.
    preview = svc._generate_appointments.call_args.args[2]
    new = preview["sessions"]
    assert len(new) == 11
    assert new[0] == {"session_number": 9, "planned_date": monday}
    svc.sessions.move_to_protocol.assert_any_await("new-id", [("b1", 10)])
    assert all(d["planned_date"].weekday() in (0, 2, 4) for d in new)  # 3x/week = Mon/Wed/Fri
    svc.sessions.relink_unstarted.assert_not_awaited()
    svc.sessions.cancel_planned.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_count_cannot_drop_below_what_is_delivered_and_booked():
    target = _target()
    svc = _replan_svc(target, _raw(target), delivered=15)
    with pytest.raises(ValidationError) as exc:
        await _amend(svc, target, _body(session_count=12))
    assert exc.value.code == "SESSION_COUNT_TOO_LOW"


@pytest.mark.asyncio
async def test_a_new_device_needs_its_own_montage():
    target = _target()
    svc = _svc(target, _raw(target))
    svc._prescribable_device = AsyncMock(return_value={"modality": "tDCS"})
    with pytest.raises(ValidationError) as exc:
        await _amend(svc, target, _body(device_id=uuid4()))
    assert exc.value.code == "MONTAGE_REQUIRED_FOR_DEVICE"


@pytest.mark.asyncio
async def test_device_change_refused_while_sessions_are_booked_on_the_old_one():
    target = _target()
    booked = [{"appointment_id": "b1", "appointment_type": "device_session", "appointment_date": dt.date.today(), "session_number": 9}]
    svc = _replan_svc(target, _raw(target), booked=booked)
    svc._prescribable_device = AsyncMock(return_value={"modality": "tDCS"})
    svc._montage_columns = AsyncMock(return_value={"tdcs_placement_id": "p2", "tdcs_dosing_id": "d2"})
    with pytest.raises(BusinessRuleError) as exc:
        await _amend(svc, target, _body(device_id=uuid4(), placement_id=uuid4(), dosing_id=uuid4()))
    assert exc.value.code == "BOOKED_SESSIONS_ON_DEVICE"


@pytest.mark.asyncio
async def test_montage_change_replaces_the_placement_columns_and_keeps_sessions():
    target = _target()
    raw = _raw(target)
    svc = _svc(target, raw)
    svc._montage_columns = AsyncMock(return_value={"custom_montage_id": "cm1"})
    await _amend(svc, target, _body(custom_montage_id=uuid4()))
    payload = svc.repo.create.call_args.args[0]
    assert payload["custom_montage_id"] == "cm1"
    assert payload["tdcs_placement_id"] is None and payload["tdcs_dosing_id"] is None
    svc.sessions.relink_unstarted.assert_awaited_once()  # same device, same schedule: sessions carry over


def test_amend_body_refuses_two_montage_sources():
    with pytest.raises(ValueError):
        s.ProtocolAmend(reason="x", clinical_note="y", custom_montage_id=uuid4(), placement_id=uuid4(), dosing_id=uuid4())
