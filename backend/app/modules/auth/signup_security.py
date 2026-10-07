"""Checks on the public patient self-signup endpoints, before any OTP is sent
(design: Documents/design_signup_auto_approval.md).

Only those anonymous endpoints are guarded. A patient registered by staff goes
through the staff endpoints, which never call anything in this module.

Every decision is one row in ops.signup_security_log: the counter the rate
limits read and the audit trail at once. Rows are written in a transaction of
their own, so a refusal is recorded even though the request itself then fails.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import re
from pathlib import Path

from email_validator import EmailNotValidError, EmailUndeliverableError, validate_email
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import get_settings
from app.core.db import system_transaction
from app.core.exceptions import AnavaException, RateLimitError, ValidationError
from app.modules.auth.signup import contact_taken_error

settings = get_settings()

# github.com/disposable-email-domains/disposable-email-domains (CC0). To
# refresh, replace the file with that repo's disposable_email_blocklist.conf.
_DISPOSABLE_DOMAINS = frozenset(Path(__file__).with_name("disposable_email_domains.txt").read_text(encoding="utf-8").split())

# Digits after the country code, where the country fixes it.
_NATIONAL_DIGITS = {"+91": 10}

# (reason stored in the log, error returned to the caller)
Refusal = tuple[str, AnavaException]


def public_ip(address: str | None) -> str | None:
    """The address, if it says who is calling. One that is not a public
    internet address (the load balancer's own, localhost) is shared by every
    caller: no per-IP rule may use it, and it is not stored."""
    try:
        return address if address and ipaddress.ip_address(address).is_global else None
    except ValueError:
        return None


def contact_hash(normalized_contact: str) -> str:
    """Keyed one-way hash: the log can count and trace a contact without ever
    holding it. Keyed, because a bare hash of a phone number is reversible by
    trying every number."""
    key = (settings.signup_hash_secret or "local-dev-only").encode()
    return hmac.new(key, normalized_contact.encode(), hashlib.sha256).hexdigest()


def normalize_phone(raw: str) -> str:
    """The number in E.164, or ValidationError if its country is not allowed."""
    codes = settings.signup_allowed_phone_country_codes
    number = re.sub(r"[\s\-()]", "", raw)
    if not number.startswith("+"):
        number = codes[0] + number.lstrip("0")
    code = next((c for c in codes if number.startswith(c)), None)
    national = number[len(code) :] if code else ""
    length_ok = len(national) == _NATIONAL_DIGITS[code] if code in _NATIONAL_DIGITS else 6 <= len(national) <= 12
    if code is None or not national.isdigit() or not length_ok:
        raise ValidationError("Enter a valid mobile number from a supported country", code="PHONE_NOT_ALLOWED")
    return number


def cognito_username(contact: str) -> str:
    """The contact exactly as guard_signup_start handed it to Cognito, for the
    later steps of the same signup (resend, verify, confirm)."""
    contact = contact.strip()
    if "@" in contact:
        return contact
    try:
        return normalize_phone(contact)
    except ValidationError:
        return contact


def is_disposable(email: str) -> bool:
    return email.rsplit("@", 1)[-1].lower() in _DISPOSABLE_DOMAINS


def _receives_mail(email: str) -> bool:
    """False when DNS says the domain cannot receive mail (no such domain, no
    mail server). A lookup that times out or gets no answer counts as
    deliverable: a DNS hiccup must not turn a real patient away."""
    try:
        validate_email(email, check_deliverability=True, timeout=3)
    except EmailUndeliverableError:
        return False
    return True


def _contact_refusal(method: str, contact: str) -> tuple[str, Refusal | None]:
    """Format checks that need no lookup. Returns the contact to use from here
    on (a phone number comes back in E.164) and why it is refused, if it is."""
    if method == "mobile":
        try:
            return normalize_phone(contact), None
        except ValidationError as exc:
            return contact, ("phone_not_allowed", exc)
    contact = contact.strip()
    try:
        validate_email(contact, check_deliverability=False)
    except EmailNotValidError:
        return contact, ("invalid_email", ValidationError("Enter a valid email address", code="INVALID_EMAIL"))
    if is_disposable(contact):
        error = ValidationError("Temporary email addresses are not accepted. Use your regular email.", code="DISPOSABLE_EMAIL")
        return contact, ("disposable_email", error)
    return contact, None


async def _comparison_key(conn: AsyncConnection, contact: str) -> tuple[str, bool]:
    """What a contact is counted and compared by, and whether an account
    already has it: the normalized mailbox (ops.normalize_email) for an email,
    the number itself for a phone."""
    if "@" in contact:
        sql = (
            "SELECT ops.normalize_email(:contact) AS key, "
            "EXISTS (SELECT 1 FROM profiles WHERE email_normalized = ops.normalize_email(:contact)) AS taken"
        )
    else:
        sql = "SELECT CAST(:contact AS TEXT) AS key, EXISTS (SELECT 1 FROM profiles WHERE phone = :contact) AS taken"
    row = (await conn.execute(text(sql), {"contact": contact})).one()
    return row.key, row.taken


async def _attempts_from_ip(conn: AsyncConnection, checkpoint: str, ip: str) -> int:
    result = await conn.execute(
        text(
            "SELECT count(*) FROM signup_security_log WHERE outcome = 'allowed' AND checkpoint = :checkpoint "
            "AND ip = CAST(:ip AS INET) AND created_at > now() - interval '1 hour'"
        ),
        {"checkpoint": checkpoint, "ip": ip},
    )
    return result.scalar_one()


async def _codes_sent_to(conn: AsyncConnection, digest: str) -> int:
    result = await conn.execute(
        text(
            "SELECT count(*) FROM signup_security_log WHERE outcome = 'allowed' "
            "AND checkpoint IN ('signup_start', 'signup_resend') AND contact_hash = :digest "
            "AND created_at > now() - interval '1 hour'"
        ),
        {"digest": digest},
    )
    return result.scalar_one()


async def _conclude(checkpoint: str, refusal: Refusal | None, ip: str | None, digest: str | None) -> None:
    """Writes the decision and, if it was a refusal, raises it. Only an
    allowed attempt counts towards a limit, so someone who keeps retrying
    while blocked does not push their own unblock time further away."""
    async with system_transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO signup_security_log (checkpoint, outcome, reason, ip, contact_hash) "
                "VALUES (:checkpoint, :outcome, :reason, CAST(:ip AS INET), :digest)"
            ),
            {
                "checkpoint": checkpoint,
                "outcome": "blocked" if refusal else "allowed",
                "reason": refusal[0] if refusal else None,
                "ip": ip,
                "digest": digest,
            },
        )
    if refusal:
        raise refusal[1]


def _too_many_codes() -> Refusal:
    error = RateLimitError("Too many codes requested for this email or phone. Try again in an hour.", code="SIGNUP_RATE_LIMITED")
    return ("contact_rate_limit", error)


async def guard_signup_start(*, ip: str | None, method: str, contact: str) -> str:
    """Every pre-OTP check for one signup attempt. Returns the contact to hand
    to Cognito (a phone number comes back in E.164); raises if it is refused.

    ponytail: count, then insert, without a lock — two requests landing in
    the same instant can both take the last slot. The WAF handles floods;
    this handles slow abuse. Take an advisory lock per IP if it ever matters."""
    ip = public_ip(ip)
    contact, refusal = _contact_refusal(method, contact)
    digest = None
    async with system_transaction() as conn:
        if ip and await _attempts_from_ip(conn, "signup_start", ip) >= settings.signup_start_limit_per_ip_per_hour:
            refusal = (
                "ip_rate_limit",
                RateLimitError("Too many signups from this network. Try again in an hour.", code="SIGNUP_RATE_LIMITED"),
            )
        elif refusal is None:
            key, taken = await _comparison_key(conn, contact)
            digest = contact_hash(key)
            if await _codes_sent_to(conn, digest) >= settings.signup_contact_limit_per_hour:
                refusal = _too_many_codes()
            elif taken:
                refusal = ("contact_taken", contact_taken_error(method, contact))
    if refusal is None and method == "email" and not await asyncio.to_thread(_receives_mail, contact):
        error = ValidationError("This email domain cannot receive mail. Check the address.", code="EMAIL_UNDELIVERABLE")
        refusal = ("undeliverable_email", error)
    await _conclude("signup_start", refusal, ip, digest)
    return contact


async def guard_signup_resend(*, ip: str | None, contact: str) -> str:
    """A resend sends another code to the same contact, so it shares the
    per-contact limit with signup_start. Returns the contact as Cognito knows it."""
    contact = cognito_username(contact)
    async with system_transaction() as conn:
        key, _ = await _comparison_key(conn, contact)
        digest = contact_hash(key)
        refusal = _too_many_codes() if await _codes_sent_to(conn, digest) >= settings.signup_contact_limit_per_hour else None
    await _conclude("signup_resend", refusal, public_ip(ip), digest)
    return contact


async def guard_otp_attempt(*, ip: str | None) -> None:
    """Caps OTP guesses from one address, on top of Cognito's own per-user limit."""
    ip = public_ip(ip)
    refusal = None
    if ip:
        async with system_transaction() as conn:
            if await _attempts_from_ip(conn, "otp_confirm", ip) >= settings.signup_otp_limit_per_ip_per_hour:
                refusal = (
                    "otp_rate_limit",
                    RateLimitError("Too many verification attempts. Try again in an hour.", code="OTP_RATE_LIMITED"),
                )
    await _conclude("otp_confirm", refusal, ip, None)
