"""confirm_and_register (auth/signup.py): verify OTP + create the account in
one request, and a retry after a half-finished attempt never duplicates."""

import asyncio

import app.core.cognito as cognito
import app.modules.auth.signup as signup
from app.modules.patients import service as patients_service


class FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row

    def scalar(self):
        return None


class FakeDb:
    def __init__(self, existing):
        self.existing = existing
        self.updates = []
        self.committed = False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "cognito_sub = :sub" in sql:
            return FakeResult(self.existing)
        if sql.startswith("UPDATE profiles"):
            self.updates.append(sql)
        return FakeResult(None)

    async def commit(self):
        self.committed = True


def run(monkeypatch, existing):
    calls = {"confirm": 0, "registered": []}

    def confirm_sign_up(**k):
        calls["confirm"] += 1
        assert k["allow_already_confirmed"] is True

    monkeypatch.setattr(cognito, "confirm_sign_up", confirm_sign_up)
    token = signup.jose_jwt.encode({"sub": "sub-1"}, "k", algorithm="HS256")
    monkeypatch.setattr(cognito, "initiate_auth", lambda **k: {"AccessToken": token})

    async def register(self, data, **kw):
        calls["registered"].append((data, kw))
        return {"patient_id": "p-new", "profile_id": "pr-new"}

    monkeypatch.setattr(patients_service.PatientService, "__init__", lambda self, db: None)
    monkeypatch.setattr(patients_service.PatientService, "register", register)
    db = FakeDb(existing)
    patient, auth = asyncio.run(
        signup.confirm_and_register(
            db,
            contact="+911234567890",
            method="mobile",
            code="123456",
            password="Secret123!",
            registration={"first_name": "A"},
            self_registered=True,
        )
    )
    return patient, auth, calls, db


def test_new_account_is_created_and_channel_marked_verified(monkeypatch):
    patient, auth, calls, db = run(monkeypatch, existing=None)
    assert patient["patient_id"] == "p-new"
    data, kw = calls["registered"][0]
    assert data["phone"] == "+911234567890" and data["email"].startswith("pending-")
    assert kw["cognito_sub"] == "sub-1" and kw["self_registered"] is True
    assert "phone_verified" in db.updates[0]
    assert db.committed and "AccessToken" in auth


def test_retry_after_success_returns_existing_account(monkeypatch):
    patient, _auth, calls, db = run(monkeypatch, existing={"patient_id": "p-old", "profile_id": "pr-old"})
    assert patient["patient_id"] == "p-old"
    assert calls["registered"] == []
    assert not db.committed
