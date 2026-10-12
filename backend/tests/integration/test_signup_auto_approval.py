"""Self-signup rate limits and the automatic approval of a finished
registration, against a real database and under the same RLS roles as in the
API: the guards as anonymous callers, the approval as the patient themself."""

import random
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core.db import async_session_factory
from app.core.exceptions import ConflictError, RateLimitError, ValidationError
from app.modules.auth import signup_security as sec
from app.modules.patients.repository import PatientRepository
from app.modules.patients.service import PatientService
from tests.integration.conftest import needs_test_database

pytestmark = needs_test_database

settings = get_settings()


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setattr(sec, "_receives_mail", lambda email: True)


def _ip() -> str:
    return f"49.{random.randint(1, 250)}.{random.randint(1, 250)}.{random.randint(1, 250)}"


def _email(domain: str = "rediffmail.com") -> str:
    return f"u{uuid.uuid4().hex[:12]}@{domain}"


async def _log(admin, ip: str) -> list[tuple[str, str | None]]:
    rows = await admin("SELECT outcome, reason FROM signup_security_log WHERE ip = CAST(:ip AS INET) ORDER BY created_at", ip=ip)
    return [(row.outcome, row.reason) for row in rows]


# ── stage 1: at signup ───────────────────────────────────────────────────────


async def test_signups_from_one_address_are_capped_and_blocked_tries_do_not_count(admin, monkeypatch):
    monkeypatch.setattr(settings, "signup_start_limit_per_ip_per_hour", 3)
    ip = _ip()

    for _ in range(3):
        await sec.guard_signup_start(ip=ip, method="email", contact=_email())
    for _ in range(2):
        with pytest.raises(RateLimitError) as err:
            await sec.guard_signup_start(ip=ip, method="email", contact=_email())
        assert err.value.status_code == 429
    await sec.guard_signup_start(ip=_ip(), method="email", contact=_email())  # another address is unaffected

    assert await _log(admin, ip) == [("allowed", None)] * 3 + [("blocked", "ip_rate_limit")] * 2


async def test_codes_to_one_mailbox_are_capped_across_its_aliases(monkeypatch):
    monkeypatch.setattr(settings, "signup_contact_limit_per_hour", 2)
    name = uuid.uuid4().hex[:10]

    await sec.guard_signup_start(ip=_ip(), method="email", contact=f"{name[:5]}.{name[5:]}+a@gmail.com")
    await sec.guard_signup_resend(ip=_ip(), contact=f"{name}+b@googlemail.com")

    with pytest.raises(RateLimitError):
        await sec.guard_signup_start(ip=_ip(), method="email", contact=f"{name.upper()}@gmail.com")
    with pytest.raises(RateLimitError):
        await sec.guard_signup_resend(ip=_ip(), contact=f"{name}@gmail.com")


async def test_alias_of_a_registered_mailbox_is_refused(admin):
    name, ip = uuid.uuid4().hex[:10], _ip()
    await admin(
        "INSERT INTO profiles (cognito_sub, email, first_name, last_name, role) VALUES (:sub, :email, 'Has', 'Account', 'patient')",
        sub=f"test-{name}",
        email=f"{name[:5]}.{name[5:]}@gmail.com",
    )

    with pytest.raises(ConflictError) as err:
        await sec.guard_signup_start(ip=ip, method="email", contact=f"{name}+second@gmail.com")

    assert err.value.code == "EMAIL_ALREADY_EXISTS"
    assert await _log(admin, ip) == [("blocked", "contact_taken")]
    await admin("DELETE FROM profiles WHERE cognito_sub = :sub", sub=f"test-{name}")


async def test_bad_contacts_are_refused_and_logged_without_the_contact_itself(admin, monkeypatch):
    ip = _ip()

    with pytest.raises(ValidationError) as err:
        await sec.guard_signup_start(ip=ip, method="email", contact="someone@mailinator.com")
    assert err.value.code == "DISPOSABLE_EMAIL"

    monkeypatch.setattr(sec, "_receives_mail", lambda email: False)
    with pytest.raises(ValidationError) as err:
        await sec.guard_signup_start(ip=ip, method="email", contact=_email())
    assert err.value.code == "EMAIL_UNDELIVERABLE"

    with pytest.raises(ValidationError) as err:
        await sec.guard_signup_start(ip=ip, method="mobile", contact="+14155550123")
    assert err.value.code == "PHONE_NOT_ALLOWED"

    assert await _log(admin, ip) == [("blocked", "disposable_email"), ("blocked", "undeliverable_email"), ("blocked", "phone_not_allowed")]
    hashes = [row.contact_hash for row in await admin("SELECT contact_hash FROM signup_security_log WHERE ip = CAST(:ip AS INET)", ip=ip)]
    assert all(h is None or (len(h) == 64 and "@" not in h) for h in hashes)


async def test_phone_is_handed_on_in_e164():
    typed = f"9{random.randint(10**8, 10**9 - 1)}"
    assert await sec.guard_signup_start(ip=_ip(), method="mobile", contact=f"{typed[:5]} {typed[5:]}") == f"+91{typed}"


async def test_otp_attempts_from_one_address_are_capped(admin, monkeypatch):
    monkeypatch.setattr(settings, "signup_otp_limit_per_ip_per_hour", 2)
    ip = _ip()

    await sec.guard_otp_attempt(ip=ip)
    await sec.guard_otp_attempt(ip=ip)
    with pytest.raises(RateLimitError) as err:
        await sec.guard_otp_attempt(ip=ip)

    assert err.value.code == "OTP_RATE_LIMITED"
    assert (await _log(admin, ip))[-1] == ("blocked", "otp_rate_limit")


async def test_load_balancer_address_is_never_rate_limited_or_stored(admin, monkeypatch):
    """Until the real client address is available, every caller shares the
    balancer's. A per-IP limit on it would lock everyone out together."""
    monkeypatch.setattr(settings, "signup_start_limit_per_ip_per_hour", 0)
    contact = _email()

    await sec.guard_signup_start(ip="172.31.9.71", method="email", contact=contact)

    row = (await admin("SELECT outcome, ip FROM signup_security_log WHERE contact_hash = :h", h=sec.contact_hash(contact))).one()
    assert (row.outcome, row.ip) == ("allowed", None)


# ── stage 3: when registration completes ─────────────────────────────────────


@pytest.fixture
async def make_patient(admin):
    """Creates a self-registered patient whose wizard has just completed, at a
    clinic of this test's own. Rows are left in place: the audit trail
    references them, as it does for real patients, and the test database is
    thrown away after the run."""
    clinic_id, region_id = str(uuid.uuid4()), str(uuid.uuid4())
    await admin(
        "INSERT INTO regions (region_id, region_name, country, state) VALUES (:id, :name, 'IN', :name)",
        id=region_id,
        name=f"r-{region_id[:8]}",
    )
    await admin(
        "INSERT INTO clinics (clinic_id, clinic_code, clinic_name, clinic_type, region_id) "
        "VALUES (:id, :code, 'Test clinic', 'clinic', :region)",
        id=clinic_id,
        code=f"T{clinic_id[:8]}",
        region=region_id,
    )

    async def make(*, first_name="Asha", last_name="Rao", dob=date(1990, 5, 17), minutes_in_wizard=10, signup_ip=None) -> dict:
        ids = {"profile_id": str(uuid.uuid4()), "patient_id": str(uuid.uuid4())}
        await admin(
            "INSERT INTO profiles (id, cognito_sub, email, first_name, last_name, dob, role, is_active) "
            "VALUES (:id, :sub, :email, :first, :last, :dob, 'patient', FALSE)",
            id=ids["profile_id"],
            sub=f"test-{ids['profile_id']}",
            email=_email(),
            first=first_name,
            last=last_name,
            dob=dob,
        )
        await admin(
            "INSERT INTO patients (patient_id, profile_id, mrn, primary_clinic_id, self_registered, approval_status, "
            "registration_status, registration_completed_at, created_at, signup_ip) "
            "VALUES (:id, :profile, :mrn, :clinic, TRUE, 'pending', 'registration_complete', now(), "
            "now() - make_interval(mins => :minutes), CAST(:ip AS INET))",
            id=ids["patient_id"],
            profile=ids["profile_id"],
            mrn=f"T{ids['patient_id'][:10]}",
            clinic=clinic_id,
            minutes=minutes_in_wizard,
            ip=signup_ip,
        )
        return ids

    return make


async def _review(ids: dict) -> dict:
    """Runs the review as the API does: in the patient's own request. Returns
    the patient as the API then reads it."""
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_user_id', :id, true), set_config('app.current_user_role', 'patient', true)"),
            {"id": ids["profile_id"]},
        )
        await PatientService(session)._review_self_registration(uuid.UUID(ids["patient_id"]))
        return await PatientRepository(session).get(uuid.UUID(ids["patient_id"]))


async def _review_log(admin, ids: dict) -> list[tuple[str, str | None]]:
    rows = await admin(
        "SELECT outcome, reason FROM signup_security_log WHERE patient_id = :id AND checkpoint = 'registration_complete'",
        id=ids["patient_id"],
    )
    return sorted((row.outcome, row.reason) for row in rows)


async def test_clean_registration_is_approved_by_the_system(admin, make_patient, monkeypatch):
    monkeypatch.setattr(settings, "auto_approve_self_registration", True)
    ids = await make_patient(signup_ip="49.36.7.7")

    patient = await _review(ids)

    assert (patient["approval_status"], patient["approval_method"], patient["approved_by"]) == ("approved", "auto", None)
    assert patient["approved_by_name"] == "System"
    assert patient["approved_at"] is not None
    assert patient["risk_flags"] == []
    assert patient["profile_is_active"] is True
    assert await _review_log(admin, ids) == [("auto_approved", None)]
    event = (
        await admin(
            "SELECT payload FROM outbox_events WHERE aggregate_id = :id AND event_type = 'patient_registration_decided'",
            id=ids["patient_id"],
        )
    ).scalar_one()
    assert (event["decision"], event["method"]) == ("approved", "auto")


async def test_clean_registration_waits_for_a_receptionist_while_the_feature_is_off(admin, make_patient):
    ids = await make_patient()

    patient = await _review(ids)

    assert (patient["approval_status"], patient["approval_method"]) == ("pending", None)
    assert patient["risk_flags"] == []
    assert patient["profile_is_active"] is False
    assert await _review_log(admin, ids) == []


@pytest.mark.parametrize(
    ("flag", "overrides"),
    [
        ("completed_too_fast", {"minutes_in_wizard": 0}),
        ("suspicious_name", {"first_name": "Test"}),
        ("implausible_dob", {"dob": date(1850, 1, 1)}),
    ],
)
async def test_flagged_registration_is_held_for_review_never_rejected(admin, make_patient, monkeypatch, flag, overrides):
    monkeypatch.setattr(settings, "auto_approve_self_registration", True)
    ids = await make_patient(**overrides)

    patient = await _review(ids)

    assert patient["approval_status"] == "pending"
    assert patient["risk_flags"] == [flag]
    assert patient["profile_is_active"] is False
    assert await _review_log(admin, ids) == [("flagged", flag)]


async def test_same_name_and_birth_date_at_the_same_clinic_is_flagged(make_patient, monkeypatch):
    monkeypatch.setattr(settings, "auto_approve_self_registration", True)
    await make_patient(first_name="Kiran", last_name="Shetty")
    second = await make_patient(first_name=" kiran", last_name="SHETTY")

    assert (await _review(second))["risk_flags"] == ["duplicate_patient"]


async def test_too_many_registrations_from_one_address_are_flagged(make_patient, monkeypatch):
    monkeypatch.setattr(settings, "auto_approve_self_registration", True)
    monkeypatch.setattr(settings, "signup_ip_velocity_limit_per_day", 1)
    ip = _ip()
    await make_patient(first_name="Kiran", signup_ip=ip)
    second = await make_patient(first_name="Divya", signup_ip=ip)

    assert (await _review(second))["risk_flags"] == ["ip_velocity"]


async def test_every_failed_check_is_recorded(admin, make_patient, monkeypatch):
    monkeypatch.setattr(settings, "auto_approve_self_registration", True)
    ids = await make_patient(first_name="Test", minutes_in_wizard=0)

    patient = await _review(ids)

    assert patient["risk_flags"] == ["suspicious_name", "completed_too_fast"]
    assert await _review_log(admin, ids) == [("flagged", "completed_too_fast"), ("flagged", "suspicious_name")]


# ── the endpoint itself ──────────────────────────────────────────────────────


async def test_signup_endpoint_checks_the_real_caller_behind_the_load_balancer(admin, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.modules.auth import router as auth_router

    monkeypatch.setattr(settings, "auth_mode", "cognito")
    monkeypatch.setattr(settings, "trust_forwarded_for", True)
    monkeypatch.setattr(settings, "signup_start_limit_per_ip_per_hour", 1)
    handed_to_cognito = []

    async def fake_cognito_signup(db, *, contact, **_):
        handed_to_cognito.append(contact)

    monkeypatch.setattr(auth_router, "start_patient_signup", fake_cognito_signup)
    ip, number = _ip(), f"9{random.randint(10**8, 10**9 - 1)}"
    body = {
        "first_name": "Asha",
        "last_name": "Rao",
        "primary_clinic_id": str(uuid.uuid4()),
        "method": "mobile",
        "contact": f"{number[:5]} {number[5:]}",
        "password": "Str0ng!Passw0rd",
        "confirm_password": "Str0ng!Passw0rd",
    }
    # What the balancer sends on: anything the client claimed, then the address it really saw.
    headers = {"X-Forwarded-For": f"1.2.3.4, {ip}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first = await http.post("/api/v1/auth/patients/signup/start", json=body, headers=headers)
        second = await http.post("/api/v1/auth/patients/signup/start", json={**body, "contact": "+919000000001"}, headers=headers)

    assert first.status_code == 204
    assert handed_to_cognito == [f"+91{number}"]
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "SIGNUP_RATE_LIMITED"
    assert await _log(admin, ip) == [("allowed", None), ("blocked", "ip_rate_limit")]
