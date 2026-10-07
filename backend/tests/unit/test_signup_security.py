"""The parts of the self-signup checks that need no database: which address
counts as the caller's, phone and email format rules, the contact hash, and
the name / date-of-birth risk rules. The rate limits and the automatic
approval run against a real database in tests/integration/test_signup_auto_approval.py."""

from datetime import date

import pytest
from starlette.requests import Request

from app.core import middleware
from app.core.exceptions import ValidationError
from app.modules.auth import signup_security as sec
from app.modules.patients import registration_risk as risk


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 5000), "headers": headers})


# ── whose address is it ──────────────────────────────────────────────────────


def test_forwarded_header_is_ignored_unless_the_load_balancer_is_trusted(monkeypatch):
    monkeypatch.setattr(middleware.settings, "trust_forwarded_for", False)
    assert middleware.client_ip(_request("10.0.1.5", "49.36.1.1")) == "10.0.1.5"


def test_behind_the_load_balancer_the_caller_is_the_last_forwarded_hop(monkeypatch):
    monkeypatch.setattr(middleware.settings, "trust_forwarded_for", True)
    # A client can prepend anything; the balancer appends the real address last.
    assert middleware.client_ip(_request("10.0.1.5", "1.2.3.4, 49.36.1.1")) == "49.36.1.1"
    assert middleware.client_ip(_request("10.0.1.5")) == "10.0.1.5"


@pytest.mark.parametrize("address", ["10.0.1.5", "172.31.9.71", "127.0.0.1", "not-an-ip", "", None])
def test_address_that_is_not_a_public_one_identifies_nobody(address):
    assert sec.public_ip(address) is None


def test_public_address_is_kept():
    assert sec.public_ip("49.36.1.1") == "49.36.1.1"


# ── phone ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("typed", ["+919876543210", "98765 43210", "09876543210", "+91 98765-43210"])
def test_indian_number_is_normalized_to_e164(typed):
    assert sec.normalize_phone(typed) == "+919876543210"


@pytest.mark.parametrize("typed", ["+14155550123", "+9198765", "+91987654321012", "+91abcdefghij"])
def test_other_countries_and_malformed_numbers_are_refused(typed):
    with pytest.raises(ValidationError) as err:
        sec.normalize_phone(typed)
    assert err.value.code == "PHONE_NOT_ALLOWED"


def test_another_country_can_be_allowed_by_configuration(monkeypatch):
    monkeypatch.setattr(sec.settings, "signup_allowed_phone_country_codes", ["+91", "+1"])
    assert sec.normalize_phone("+14155550123") == "+14155550123"


def test_later_signup_steps_use_the_same_username_as_the_first():
    assert sec.cognito_username(" 98765 43210 ") == "+919876543210"
    assert sec.cognito_username(" Someone@Example.com ") == "Someone@Example.com"


# ── email ────────────────────────────────────────────────────────────────────


def test_throwaway_domain_is_refused_and_an_ordinary_one_is_not():
    assert sec._contact_refusal("email", "someone@mailinator.com")[1][0] == "disposable_email"
    assert sec._contact_refusal("email", "someone@gmail.com")[1] is None


@pytest.mark.parametrize("typed", ["no-at-sign", "two@@example.com", "a@b", "x" * 300 + "@gmail.com"])
def test_malformed_or_overlong_email_is_refused(typed):
    assert sec._contact_refusal("email", typed)[1][0] == "invalid_email"


# ── contact hash ─────────────────────────────────────────────────────────────


def test_contact_hash_is_stable_keyed_and_does_not_contain_the_contact(monkeypatch):
    digest = sec.contact_hash("+919876543210")
    assert digest == sec.contact_hash("+919876543210")
    assert digest != sec.contact_hash("+919876543211")
    assert "9876543210" not in digest
    monkeypatch.setattr(sec.settings, "signup_hash_secret", "another-key")
    assert sec.contact_hash("+919876543210") != digest


# ── registration risk rules ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("first", "last"),
    [("Test", "User"), ("asdf", "Kumar"), ("Ravi2", "Kumar"), ("Raaavi", "Kumar"), ("R", "Kumar"), ("Ravi", "Kummmar")],
)
def test_junk_names_are_flagged(first, last):
    assert risk.name_is_suspicious(first, last) is True


@pytest.mark.parametrize(("first", "last"), [("Mohan", "A"), ("Asha", "Rao"), ("Sree", "Lakshmi"), ("Mary Ann", "D'Souza")])
def test_real_names_including_a_single_letter_surname_are_not_flagged(first, last):
    assert risk.name_is_suspicious(first, last) is False


def test_date_of_birth_in_the_future_or_impossibly_old_is_flagged():
    today = date(2026, 10, 4)
    assert risk.dob_is_implausible(date(2026, 10, 5), today) is True
    assert risk.dob_is_implausible(date(1870, 1, 1), today) is True
    assert risk.dob_is_implausible(date(1930, 1, 1), today) is False
    assert risk.dob_is_implausible(None, today) is False
