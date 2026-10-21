"""First treatment protocol waits on the initial consultation's anamnesis
(required questions), PRS and doctor's note. Amendments and protocols after
treatment has started are not gated. Mocks the DB so none is needed."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import BusinessRuleError
from app.modules.treatment_protocols.service import ProtocolService


def _row(**over) -> dict:
    return {
        "appointment_id": uuid4(),
        "anamnesis_id": "ANA-1",
        "missing_questions": [],
        "prs_completed": True,
        "notes_count": 1,
        "has_started_treatment": False,
        **over,
    }


async def _readiness(row: dict) -> dict:
    svc = ProtocolService(AsyncMock())
    with patch.object(svc.repo, "initial_consultation_readiness", AsyncMock(return_value=row)):
        return await svc.readiness(uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("over", "missing"),
    [
        ({}, []),
        ({"missing_questions": ["chief_complaint"]}, ["anamnesis"]),
        ({"prs_completed": False}, ["prs"]),
        ({"notes_count": 0}, ["doctor_notes"]),
        (
            {"appointment_id": None, "missing_questions": ["chief_complaint"], "prs_completed": False, "notes_count": 0},
            ["initial_consultation", "anamnesis", "prs", "doctor_notes"],
        ),
    ],
)
async def test_all_three_are_required(over, missing):
    r = await _readiness(_row(**over))
    assert r["missing"] == missing
    assert r["ready"] is (not missing)


@pytest.mark.asyncio
async def test_not_gated_once_treatment_has_started():
    r = await _readiness(_row(has_started_treatment=True, prs_completed=False, notes_count=0))
    assert r["gate_applies"] is False and r["ready"] is True


def _ctx() -> RequestContext:
    return RequestContext(user_id=str(uuid4()), role="doctor", clinic_id=str(uuid4()), region_id=None)


def _svc() -> ProtocolService:
    svc = ProtocolService(AsyncMock())
    svc._resolve_parent = AsyncMock(return_value={"patient_id": uuid4(), "clinic_id": uuid4()})
    return svc


@pytest.mark.asyncio
async def test_first_protocol_refused_until_ready():
    svc = _svc()
    not_ready = {"ready": False, "missing": ["anamnesis", "prs"], "missing_questions": ["chief_complaint"]}
    with (
        patch("app.modules.treatment_protocols.service.assert_clinic_scope", AsyncMock()),
        patch.object(svc, "readiness", AsyncMock(return_value=not_ready)),
    ):
        with pytest.raises(BusinessRuleError) as exc:
            await svc.create(MagicMock(supersedes_protocol_id=None), _ctx())
    assert exc.value.code == "PROTOCOL_PREREQUISITES_MISSING"
    assert [d["field"] for d in exc.value.details] == ["anamnesis", "prs"]
    assert exc.value.details[0]["missing_questions"] == ["chief_complaint"]


@pytest.mark.asyncio
async def test_amendment_skips_the_gate():
    svc = _svc()
    readiness = AsyncMock()
    past = RuntimeError("past the gate")
    with (
        patch("app.modules.treatment_protocols.service.assert_clinic_scope", AsyncMock()),
        patch.object(svc, "readiness", readiness),
        patch.object(svc, "_prescribable_device", AsyncMock(side_effect=past)),
    ):
        with pytest.raises(RuntimeError):
            await svc.create(MagicMock(supersedes_protocol_id=uuid4()), _ctx())
    readiness.assert_not_called()
