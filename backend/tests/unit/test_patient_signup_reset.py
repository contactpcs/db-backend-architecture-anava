"""Abandoned-signup recovery (auth/signup.py start_patient_signup).

A Cognito user for this contact with no profile in our DB is an abandoned
signup (quit before the OTP, or — legacy flow — after the OTP but before the
password). Retrying must reset it and send a fresh OTP; a real account (one
with a profile) must never be deleted.
"""

import asyncio

import pytest

import app.core.cognito as cognito
from app.core.exceptions import BusinessRuleError
from app.modules.auth.signup import start_patient_signup


class FakeResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row

    def scalar(self):
        return self.row


class FakeDb:
    def __init__(self, has_profile: bool):
        self.has_profile = has_profile

    async def execute(self, stmt, params=None):
        if "cognito_sub" in str(stmt):
            return FakeResult((1,) if self.has_profile else None)
        return FakeResult(None)  # as_system's set_config / current_setting


KW = {
    "contact": "a@b.com",
    "method": "email",
    "first_name": "A",
    "last_name": "B",
    "dob": "2000-01-01",
    "gender": None,
    "password": "Secret123!",
}


def run(monkeypatch, *, first_signup_fails: bool, sub: str | None, has_profile: bool):
    calls = {"signup": 0, "deleted": 0}

    def sign_up_patient(**_k):
        calls["signup"] += 1
        if first_signup_fails and calls["signup"] == 1:
            raise BusinessRuleError("exists", code="ACCOUNT_ALREADY_EXISTS")

    def delete_user(_u):
        calls["deleted"] += 1

    monkeypatch.setattr(cognito, "sign_up_patient", sign_up_patient)
    monkeypatch.setattr(cognito, "get_user_sub", lambda _u: sub)
    monkeypatch.setattr(cognito, "delete_user", delete_user)
    asyncio.run(start_patient_signup(FakeDb(has_profile), **KW))
    return calls


def test_fresh_signup_just_signs_up(monkeypatch):
    assert run(monkeypatch, first_signup_fails=False, sub=None, has_profile=False) == {"signup": 1, "deleted": 0}


def test_abandoned_signup_is_reset_and_restarted(monkeypatch):
    assert run(monkeypatch, first_signup_fails=True, sub="sub-1", has_profile=False) == {"signup": 2, "deleted": 1}


def test_real_account_is_never_deleted(monkeypatch):
    with pytest.raises(BusinessRuleError) as err:
        run(monkeypatch, first_signup_fails=True, sub="sub-1", has_profile=True)
    assert err.value.code == "ACCOUNT_ALREADY_EXISTS"


def test_other_signup_errors_pass_through(monkeypatch):
    def boom(**_k):
        raise BusinessRuleError("down", code="COGNITO_SIGNUP_FAILED")

    monkeypatch.setattr(cognito, "sign_up_patient", boom)
    with pytest.raises(BusinessRuleError) as err:
        asyncio.run(start_patient_signup(FakeDb(False), **KW))
    assert err.value.code == "COGNITO_SIGNUP_FAILED"
