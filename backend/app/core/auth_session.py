"""Login-session plumbing that sits next to (not inside) token verification.

Two independent pieces:

1. The refresh-token cookie. The long-lived Cognito refresh token is kept in
   an httpOnly cookie so page scripts (and therefore XSS) can never read it;
   the browser only ever holds the short-lived access token.

2. Redis-backed state: a denylist of logged-out access tokens (a signed JWT
   would otherwise stay valid until it expires, however many times its owner
   logged out) and one-time tickets for opening the live-notification
   stream. Redis is already required for live push (core/pubsub.py); this
   adds no new infrastructure.
"""

from __future__ import annotations

import asyncio
import secrets
import time

import structlog
from fastapi import Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.pubsub import get_redis

logger = structlog.get_logger()
settings = get_settings()

REFRESH_COOKIE_NAME = "anava_refresh"
# Scoped to the auth endpoints only: the browser attaches this cookie to
# /auth/refresh and /auth/logout and to nothing else, so the refresh token
# never travels with an ordinary API call.
REFRESH_COOKIE_PATH = "/api/v1/auth"

_REVOKED_PREFIX = "revoked_jti:"
_TICKET_PREFIX = "sse_ticket:"

# The revocation check runs on EVERY authenticated request, so a slow or
# unreachable Redis must cost almost nothing:
#  - each call gets a hard timeout (a black-holed connection would otherwise
#    hang for the OS connect timeout, tens of seconds, on every request), and
#  - after one failure the breaker skips Redis entirely for a while, so the
#    requests behind it don't each pay that timeout in turn.
_OP_TIMEOUT_SECONDS = 1.5
_BREAKER_SECONDS = 15.0
_down_until = 0.0


def breaker_open_for_seconds() -> float:
    """0 when Redis is being used normally; otherwise how long it is still skipped."""
    return max(_down_until - time.monotonic(), 0.0)


class RedisUnavailable(Exception):
    """Redis was skipped (breaker open) or failed just now."""


async def _redis_call(operation):
    """Runs `operation` (a zero-argument async callable) with the hard timeout
    and breaker described above. Raises on failure — callers decide whether
    that means fail-open (revocation) or fail-closed (tickets)."""
    global _down_until
    if time.monotonic() < _down_until:
        raise RedisUnavailable("redis breaker open")
    try:
        return await asyncio.wait_for(operation(), timeout=_OP_TIMEOUT_SECONDS)
    except Exception as exc:
        _down_until = time.monotonic() + _BREAKER_SECONDS
        logger.warning("redis_unavailable_breaker_open", seconds=_BREAKER_SECONDS, error=repr(exc))
        raise


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

    Best-effort by design: if Redis is down the logout still succeeds for the
    user (their refresh token is revoked at Cognito and the cookie cleared);
    the only thing lost is instant rejection of this one already-issued
    access token, which lapses on its own within the hour."""
    if not jti:
        return
    ttl = max(exp - int(time.time()), 1)
    try:
        await _redis_call(lambda: get_redis().set(_REVOKED_PREFIX + jti, "1", ex=ttl))
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("token_revocation_store_failed", error=repr(exc))


async def is_access_token_revoked(jti: str | None) -> bool:
    """Fails OPEN: a Redis outage must not lock every user out of the app."""
    if not jti:
        return False
    try:
        return bool(await _redis_call(lambda: get_redis().exists(_REVOKED_PREFIX + jti)))
    except Exception:  # noqa: BLE001 — see docstring; already logged when the breaker tripped
        return False


# ─── live-stream tickets ─────────────────────────────────────────────────────


async def issue_stream_ticket(cognito_sub: str) -> str:
    """One-time, short-lived stand-in for the access token in the SSE URL.
    Raises if Redis is unreachable — there is no live stream without it
    anyway (the relay publishes through Redis), and the caller turns that
    into a 503 the client just retries later."""
    ticket = secrets.token_urlsafe(32)
    await _redis_call(lambda: get_redis().set(_TICKET_PREFIX + ticket, cognito_sub, ex=settings.stream_ticket_ttl_seconds))
    return ticket


async def consume_stream_ticket(ticket: str) -> str | None:
    """Returns the cognito_sub the ticket was issued to, and deletes it in
    the same atomic step, so a ticket that leaks into a log is already dead.
    None for unknown/expired/already-used tickets, and when Redis is down."""
    key = _TICKET_PREFIX + ticket

    async def take() -> str | None:
        async with get_redis().pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            sub, _ = await pipe.execute()
        return sub or None

    try:
        return await _redis_call(take)
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
