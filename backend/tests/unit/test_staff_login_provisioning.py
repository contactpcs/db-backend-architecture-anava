"""Staff/admin onboarding vs Cognito (core/cognito.py attach_staff_login).

The bug this guards: the Cognito user used to be created BEFORE the profiles
INSERT. A duplicate phone made the INSERT fail and roll back, but the Cognito
user stayed, so the email could never be onboarded again ("already exists").
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.core.cognito as cognito
import app.modules.staff.repository as staff_repository
from app.core.exceptions import BusinessRuleError, ConflictError


class FakeResult:
    rowcount = 1

    def __init__(self, row=None):
        self.row = row

    def first(self):
        return self.row

    def scalar(self):
        return self.row

    def mappings(self):
        return self

    def one(self):
        return self.row


class FakeSession:
    """Just enough of AsyncSession: execute() plus a real sync Session to
    hang the commit/rollback listeners on."""

    def __init__(self, *, sub_has_profile: bool = False, insert_fails: bool = False):
        self.sub_has_profile = sub_has_profile
        self.insert_fails = insert_fails
        self.sync_session = Session()
        self.statements: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        if sql.startswith("INSERT INTO profiles"):
            if self.insert_fails:
                raise IntegrityError(sql, params, Exception('duplicate key value violates unique constraint "uq_profiles_phone"'))
            return FakeResult({"id": uuid4(), "cognito_sub": "pending-x"})
        if sql.startswith("SELECT 1 FROM profiles WHERE cognito_sub"):
            return FakeResult((1,) if self.sub_has_profile else None)
        return FakeResult(None)  # as_system's set_config / current_setting, the UPDATE


PERSON = {"email": "a@b.com", "first_name": "A", "last_name": "B", "phone": "+911234567890"}


def fake_cognito(monkeypatch, *, exists_first: bool = False, existing_sub: str | None = None):
    calls = {"provisioned": 0, "deleted": 0}

    def provision_staff_user(**_k):
        calls["provisioned"] += 1
        if exists_first and calls["provisioned"] == 1:
            raise BusinessRuleError("exists", code="ACCOUNT_ALREADY_EXISTS")
        return "sub-new"

    def delete_user(_username):
        calls["deleted"] += 1

    monkeypatch.setattr(cognito, "provision_staff_user", provision_staff_user)
    monkeypatch.setattr(cognito, "get_user_sub", lambda _u: existing_sub)
    monkeypatch.setattr(cognito, "delete_user", delete_user)
    return calls


def attach(session):
    return asyncio.run(cognito.attach_staff_login(session, profile_id=uuid4(), **PERSON))


def test_duplicate_phone_never_reaches_cognito(monkeypatch):
    calls = fake_cognito(monkeypatch)
    monkeypatch.setattr(staff_repository, "settings", SimpleNamespace(auth_mode="cognito"))
    with pytest.raises(IntegrityError):
        asyncio.run(staff_repository.create_profile(FakeSession(insert_fails=True), role="clinic_admin", **PERSON))
    assert calls == {"provisioned": 0, "deleted": 0}


def test_create_profile_returns_the_real_sub(monkeypatch):
    fake_cognito(monkeypatch)
    monkeypatch.setattr(staff_repository, "settings", SimpleNamespace(auth_mode="cognito"))
    session = FakeSession()
    profile = asyncio.run(staff_repository.create_profile(session, role="clinic_admin", **PERSON))
    assert profile["cognito_sub"] == "sub-new"
    assert any(s.startswith("UPDATE profiles SET cognito_sub") for s in session.statements)


def test_leftover_cognito_user_without_a_profile_is_replaced(monkeypatch):
    calls = fake_cognito(monkeypatch, exists_first=True, existing_sub="sub-old")
    assert attach(FakeSession(sub_has_profile=False)) == "sub-new"
    assert calls == {"provisioned": 2, "deleted": 1}


def test_real_account_is_never_deleted(monkeypatch):
    calls = fake_cognito(monkeypatch, exists_first=True, existing_sub="sub-old")
    with pytest.raises(ConflictError) as err:
        attach(FakeSession(sub_has_profile=True))
    assert err.value.code == "EMAIL_ALREADY_EXISTS"
    assert calls == {"provisioned": 1, "deleted": 0}


def attached(monkeypatch):
    """A request's transaction (a real, unbound sync Session) in which a
    Cognito user has just been created."""
    calls = fake_cognito(monkeypatch)
    session = FakeSession()
    sync = session.sync_session
    sync.begin()
    attach(session)
    return calls, sync


def test_rollback_after_provisioning_deletes_the_cognito_user(monkeypatch):
    calls, sync = attached(monkeypatch)
    sync.rollback()
    sync.begin()
    sync.rollback()
    assert calls["deleted"] == 1  # once, however many rollbacks follow


def test_commit_keeps_the_cognito_user(monkeypatch):
    calls, sync = attached(monkeypatch)
    sync.commit()
    sync.begin()
    sync.rollback()  # a later transaction on the same session failing
    assert calls["deleted"] == 0


def test_savepoint_rollback_keeps_the_cognito_user(monkeypatch):
    calls, sync = attached(monkeypatch)
    sync.begin_nested().rollback()
    assert calls["deleted"] == 0
    sync.commit()
    assert calls["deleted"] == 0


def test_savepoint_release_does_not_disarm_the_cleanup(monkeypatch):
    calls, sync = attached(monkeypatch)
    sync.begin_nested().commit()
    sync.rollback()
    assert calls["deleted"] == 1
