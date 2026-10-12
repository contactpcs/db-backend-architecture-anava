"""Receptionist approval decisions (migration 94): which moves are allowed,
and that approve/reject each write only their own attribution columns.

Pure: the service's DB calls are stubbed, driven with asyncio.run.
"""

import asyncio
import uuid

import pytest

import app.core.auth_session as auth_session
import app.modules.patients.service as svc_mod
from app.core.exceptions import BusinessRuleError
from app.modules.patients.service import PatientService


class FakeRepo:
    def __init__(self):
        self.calls: list[dict] = []

    async def set_approval(self, patient_id, *, approval_status, decided_by, rejection_reason, method):
        self.calls.append({"status": approval_status, "by": decided_by, "reason": rejection_reason, "method": method})


class FakeSession:
    def __init__(self):
        self.executed: list[dict] = []

    async def execute(self, _stmt, params=None):
        self.executed.append(params or {})


def make_service(monkeypatch, current_status: str):
    signed_out: list = []

    async def no_event(*a, **k):
        return None

    async def record_sign_out(_session, profile_id):
        signed_out.append(profile_id)

    monkeypatch.setattr(svc_mod, "emit_event", no_event)
    monkeypatch.setattr(auth_session, "sign_out_profile", record_sign_out)

    svc = PatientService.__new__(PatientService)
    svc.session = FakeSession()
    svc.repo = FakeRepo()
    patient = {"registration_status": "registration_complete", "approval_status": current_status, "profile_id": uuid.uuid4()}

    async def get(_pid):
        return patient

    svc.get = get
    return svc, signed_out


def decide(svc, decision, reason=None):
    return asyncio.run(svc.decide_approval(uuid.uuid4(), decision=decision, decided_by=uuid.uuid4(), rejection_reason=reason))


@pytest.mark.parametrize(
    ("current", "decision"),
    [("pending", "approved"), ("pending", "rejected"), ("rejected", "approved")],
)
def test_allowed_decisions(monkeypatch, current, decision):
    svc, _ = make_service(monkeypatch, current)
    decide(svc, decision)
    assert svc.repo.calls[0]["status"] == decision


@pytest.mark.parametrize(
    ("current", "decision"),
    [("rejected", "rejected"), ("approved", "approved"), ("approved", "rejected")],
)
def test_refused_decisions(monkeypatch, current, decision):
    """A rejection can be reversed; an approval is final; nothing twice."""
    svc, _ = make_service(monkeypatch, current)
    with pytest.raises(BusinessRuleError) as err:
        decide(svc, decision)
    assert err.value.code == "APPROVAL_ALREADY_DECIDED"
    assert svc.repo.calls == []


def test_reject_keeps_reason_deactivates_and_signs_out(monkeypatch):
    svc, signed_out = make_service(monkeypatch, "pending")
    decide(svc, "rejected", reason="ID document unreadable")
    assert svc.repo.calls[0]["reason"] == "ID document unreadable"
    assert svc.session.executed[0]["active"] is False
    assert len(signed_out) == 1


def test_reapprove_reactivates_without_signing_out(monkeypatch):
    svc, signed_out = make_service(monkeypatch, "rejected")
    decide(svc, "approved")
    assert svc.session.executed[0]["active"] is True
    assert signed_out == []


def test_staff_decision_is_recorded_as_manual(monkeypatch):
    svc, _ = make_service(monkeypatch, "pending")
    decide(svc, "approved")
    assert svc.repo.calls[0]["method"] == "manual"
    assert svc.repo.calls[0]["by"] is not None
