"""Login-session plumbing that sits next to (not inside) token verification.

Two independent pieces:

1. The refresh-token cookie. The long-lived Cognito refresh token is kept in
   an httpOnly cookie so page scripts (and therefore XSS) can never read it;
   the browser only ever holds the short-lived access token.

2. Short-lived state in Postgres (SQL/v1/103): a denylist of logged-out
   access tokens (a signed JWT would otherwise stay valid until it expires,
   however many times its owner logged out) and one-time tickets for opening
   the live-notification stream.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets

import structlog
from fastapi import Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import live
from app.core.db import system_transaction

logger = structlog.get_logger()
settings = get_settings()

REFRESH_COOKIE_NAME = "anava_refresh"
# Scoped to the auth endpoints only: the browser attaches this cookie to
# /auth/refresh and /auth/logout and to nothing else, so the refresh token
# never travels with an ordinary API call.
REFRESH_COOKIE_PATH = "/api/v1/auth"


# ─── refresh-token cookie ────────────────────────────────────────────────────


def _cookie_secure() -> bool:
    if settings.auth_cookie_secure is not None:
        return settings.auth_cookie_secure
    return settings.environment != "local"


def _cookie_kwargs() -> dict:
    return {
        "path": REFRESH_COOKIE_PATH,
        "domain": settings.auth_cookie_domain,
        "secure": _cookie_secure(),
        "httponly": True,
        "samesite": settings.auth_cookie_samesite.lower(),
    }


def pack_refresh_cookie(username: str, refresh_token: str) -> str:
    """The cookie carries the Cognito username next to the refresh token
    because REFRESH_TOKEN_AUTH needs it for SECRET_HASH and the access token
    it would otherwise come from has expired by the time we refresh. Neither
    half is a secret the browser could abuse alone, and Cognito rejects any
    mismatched pair, so this needs no signature of its own."""
    return f"{username}|{refresh_token}"


def unpack_refresh_cookie(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    username, sep, refresh_token = value.partition("|")
    if not sep or not username or not refresh_token:
        return None
    return username, refresh_token


def set_refresh_cookie(response: Response, *, username: str, refresh_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        pack_refresh_cookie(username, refresh_token),
        max_age=settings.refresh_cookie_max_age_days * 86400,
        **_cookie_kwargs(),
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, **{k: v for k, v in _cookie_kwargs().items() if k != "httponly"})


def read_refresh_cookie(request: Request) -> tuple[str, str] | None:
    return unpack_refresh_cookie(request.cookies.get(REFRESH_COOKIE_NAME))


# ─── logged-out access tokens ────────────────────────────────────────────────


async def revoke_access_token(jti: str | None, exp: int) -> None:
    """Denylists one access token until it would have expired anyway.

    This process rejects the token at once. Every other API process is told
    by NOTIFY, and reads the table when its listener (re)connects
    (core/live.py). Best-effort by design: if the write fails the logout
    still succeeds for the user (their refresh token is revoked at Cognito
    and the cookie cleared); the only thing lost is instant rejection of this
    one already-issued access token on the other processes, and it lapses on
    its own within the hour."""
    if not jti:
        return
    live.mark_revoked(jti, exp)
    try:
        async with system_transaction() as conn:
            await conn.execute(text("DELETE FROM revoked_access_tokens WHERE expires_at < now()"))
            await conn.execute(
                text("INSERT INTO revoked_access_tokens (jti, expires_at) VALUES (:jti, to_timestamp(:exp)) ON CONFLICT (jti) DO NOTHING"),
                {"jti": jti, "exp": exp},
            )
            await conn.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": live.TOKEN_REVOKED, "payload": f"{jti} {exp}"})
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("token_revocation_store_failed", error=repr(exc))


def is_access_token_revoked(jti: str | None) -> bool:
    """Runs on every authenticated request, so it is a lookup in this
    process's own copy of the denylist, with no I/O."""
    return jti is not None and live.is_revoked(jti)


# ─── live-stream tickets ─────────────────────────────────────────────────────


def _ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


async def issue_stream_ticket(cognito_sub: str) -> str:
    """One-time, short-lived stand-in for the access token in the SSE URL.
    Only its hash is stored. Raises if the database is unreachable; the
    caller turns that into a 503 the client just retries later."""
    ticket = secrets.token_urlsafe(32)
    async with system_transaction() as conn:
        await conn.execute(text("DELETE FROM sse_tickets WHERE expires_at < now()"))
        await conn.execute(
            text(
                "INSERT INTO sse_tickets (ticket_hash, cognito_sub, expires_at) VALUES (:hash, :sub, now() + make_interval(secs => :ttl))"
            ),
            {"hash": _ticket_hash(ticket), "sub": cognito_sub, "ttl": settings.stream_ticket_ttl_seconds},
        )
    return ticket


async def consume_stream_ticket(ticket: str) -> str | None:
    """Returns the cognito_sub the ticket was issued to, and deletes it in
    the same statement, so a ticket that leaks into a log is already dead.
    None for unknown/expired/already-used tickets, and when the database is
    unreachable."""
    try:
        async with system_transaction() as conn:
            result = await conn.execute(
                text("DELETE FROM sse_tickets WHERE ticket_hash = :hash AND expires_at > now() RETURNING cognito_sub"),
                {"hash": _ticket_hash(ticket)},
            )
            return result.scalar()
    except Exception:  # noqa: BLE001 — a ticket we cannot verify is a ticket we refuse
        return None


# ─── deactivated accounts ────────────────────────────────────────────────────


async def sign_out_profile(session: AsyncSession, profile_id) -> None:
    """Cognito global sign-out for an account that was just deactivated or
    removed, so it can never mint another access token.

    profiles.is_active is what blocks the account on its very next request;
    this closes the other half — without it the account's refresh token
    would keep working (and its already-issued access token would keep
    passing signature checks) until they lapse. Best-effort and never
    raises: it must not fail the admin's deactivation."""
    if settings.auth_mode != "cognito":
        return
    from app.core.cognito import admin_sign_out

    sub = (await session.execute(text("SELECT cognito_sub FROM profiles WHERE id = :pid"), {"pid": str(profile_id)})).scalar()
    # 'pending-<uuid>' is the placeholder for an account Cognito has never seen.
    if not sub or str(sub).startswith("pending-"):
        return
    await asyncio.to_thread(admin_sign_out, str(sub))
