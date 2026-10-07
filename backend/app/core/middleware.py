import contextlib
import json
import os
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from urllib.parse import parse_qs

import structlog
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.core.auth_session import consume_stream_ticket, is_access_token_revoked
from app.core.db import RequestContext, engine, set_request_context
from app.core.exceptions import AnavaException, AuthenticationError, PermissionError_
from app.core.security import verify_token

logger = structlog.get_logger()
settings = get_settings()


def client_ip(request: Request) -> str | None:
    """The caller's address. Behind the load balancer every connection comes
    from the balancer itself; with trust_forwarded_for the address is the
    LAST entry of X-Forwarded-For — the one the balancer appended. Anything a
    client puts in that header itself sits to the left of it, so it cannot be
    forged this way."""
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else None


# Paths that never require auth. Kept short and explicit rather than a regex —
# an accidentally-too-broad pattern here is a real security bug.
PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/health/version",  # deploy check — path count only
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/v1/auth/login",
    "/api/v1/auth/login/new-password",  # completes NEW_PASSWORD_REQUIRED — no session yet either
    "/api/v1/auth/local-login",  # dev-only (Stage 13 removes this route entirely)
    # Authenticated by the httpOnly refresh cookie, not a bearer token: refresh
    # runs precisely when the access token has expired, and logout must work
    # (and clear the cookie) even with one that already has.
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/register",  # public patient self-registration — see patients module
    "/api/v1/auth/clinics",  # public clinic picker for the self-registration form
    "/api/v1/auth/config",  # public — tells the frontend which auth endpoints to call
    "/api/v1/auth/patients/signup/start",
    "/api/v1/auth/patients/signup/resend",
    "/api/v1/auth/patients/signup/verify",
    "/api/v1/auth/patients/signup/complete",
    # Current flow's final step (OTP + create account + log in) — the patient
    # has no session until this returns one.
    "/api/v1/auth/patients/signup/confirm",
    "/api/v1/auth/forgot-password/start",  # no session yet — that's the whole point
    "/api/v1/auth/forgot-password/confirm",
    "/api/v1/webhooks/razorpay",  # authenticated via HMAC signature, not a user JWT
}

# Reachable even when profiles.is_active = FALSE — a newly-registered
# staff/patient is inactive until they sign their onboarding consent, but
# they still need to authenticate, see who they are, and sign it. Anything
# not in this set is blocked with CONSENT_REQUIRED until they do.
CONSENT_FLOW_PATH_PREFIXES = (
    "/api/v1/auth/me",
    "/api/v1/consent-templates",
    "/api/v1/consent-records",
)

# A self-registered patient stays inactive through the ENTIRE 6-step
# registration machine (demographics -> disease -> consent -> anamnesis ->
# PRS -> registration_complete) — only a receptionist's later approval
# activates them (patients.self_registered/approval_status, see
# SQL/24_patient_self_registration.sql). So an inactive *patient*
# specifically needs a wider allowance than CONSENT_FLOW_PATH_PREFIXES;
# role-checks on each endpoint (require_role(...)) remain the real
# authorization boundary — this only lifts the is_active gate, scoped to
# role=='patient' below, never to inactive staff.
PATIENT_SELF_REGISTRATION_PATH_PREFIXES = (
    *CONSENT_FLOW_PATH_PREFIXES,
    "/api/v1/patients",
    "/api/v1/anamnesis",
    "/api/v1/prs-catalog",
    "/api/v1/patient-scale-assignments",
    "/api/v1/prs-assessment-instances",
    # SSE reconnects on every page nav (assessment steps included) — without
    # this an inactive mid-registration patient's EventSource 403s on every
    # single page of the registration wizard, not just once.
    "/api/v1/events/stream",
    "/api/v1/events/ticket",  # the one-time ticket the stream is opened with
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # AuthContextMiddleware runs OUTSIDE this one (added before it in
        # main.py, and Starlette executes middleware last-added-first) — for
        # an authenticated request it has already resolved this same id and
        # stashed it on request.state so the audit trigger's app.request_id
        # and this log line's request_id are the same value, not two
        # independently generated UUIDs for one request. Only a public-path
        # request (which AuthContextMiddleware skips entirely) falls through
        # to generating one here.
        request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response


async def _load_profile_and_scope(cognito_sub: str, *, request_id: str, ip_address: str | None) -> RequestContext:
    """Resolves the caller's profile + tenant scope in ONE round trip:
    ops.auth_context() (SQL/v1/101_auth_context_lookup.sql) runs the same
    profile read, staff consent self-heal / re-check, scope lookup and
    closed-clinic / inactive-region checks that used to be up to 9 separate
    statements here (API audit). Live data every request — not a cache.

    Runs in autocommit mode: the one statement is its own transaction, so a
    consent self-heal write is committed before any rejection is raised and
    sticks even when the request is then refused (as the old separate heal
    connection did). The function's set_config(..., true) calls last for that
    statement, which is all it needs them for. An explicit BEGIN and COMMIT
    around a single statement were two extra round trips on every request."""
    async with engine.connect() as plain:
        conn = await plain.execution_options(isolation_level="AUTOCOMMIT")
        row = (await conn.execute(text("SELECT * FROM ops.auth_context(:sub)"), {"sub": cognito_sub})).first()
    if row is None:
        raise PermissionError_("Profile not found", code="PROFILE_NOT_FOUND")
    if row.clinic_closed:
        raise PermissionError_("This clinic is closed", code="CLINIC_CLOSED")
    if row.region_inactive:
        raise PermissionError_("This region is inactive", code="REGION_INACTIVE")
    return RequestContext(
        user_id=str(row.profile_id),
        role=row.user_role,
        clinic_id=str(row.scope_clinic_id) if row.scope_clinic_id else None,
        region_id=str(row.scope_region_id) if row.scope_region_id else None,
        is_active=row.active,
        consent_signed=row.consent,
        request_id=request_id,
        ip_address=ip_address,
    )


def server_busy_response() -> JSONResponse:
    """No database connection came free within db_pool_timeout_seconds: the
    server is overloaded. A quick, honest 503 the client can retry beats
    holding the request (and everything queued behind it) for longer."""
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "2"},
        content={"error": {"code": "SERVER_BUSY", "message": "The server is busy. Please try again in a moment.", "details": None}},
    )


async def assert_auth_context_function() -> None:
    """Startup guard: the lookup above needs alembic 0054. Fail the boot with a
    clear message instead of returning a 500 on every authenticated request."""
    async with engine.connect() as conn:
        found = (await conn.execute(text("SELECT to_regprocedure('ops.auth_context(text)') IS NOT NULL"))).scalar()
    if not found:
        raise RuntimeError("ops.auth_context(text) is missing - run `alembic upgrade head` (0054) before starting the API")


class AuthContextMiddleware(BaseHTTPMiddleware):
    """Validates the bearer token and resolves identity/tenant scope BEFORE
    any route/permission dependency runs. Sets the RequestContext contextvar
    that core/db.py's get_db() dependency applies via SET LOCAL for RLS."""

    async def dispatch(self, request: Request, call_next):
        # Resolved here (not left to RequestIDMiddleware, which runs after
        # this one) so it's available below for the RequestContext that
        # feeds the DB audit trigger — see RequestIDMiddleware's own comment.
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        caller_ip = client_ip(request)

        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        ticket: str | None = None
        token: str | None = None
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
        elif request.url.path == "/api/v1/events/stream" and request.query_params.get("ticket"):
            # Browser EventSource can't set custom headers, so the live stream
            # is opened with a one-time, 30-second ticket minted by
            # POST /events/ticket (an authenticated call) instead of the
            # access token — which therefore never lands in a URL, and with
            # it in access logs, proxy logs or browser history. Scoped to
            # this one path.
            ticket = request.query_params["ticket"]
        else:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "MISSING_TOKEN", "message": "Authorization header required"}},
            )

        try:
            if ticket is not None:
                sub = await consume_stream_ticket(ticket)
                if sub is None:
                    raise AuthenticationError("Invalid or expired stream ticket", code="INVALID_STREAM_TICKET")
                cognito_sub = sub
            else:
                assert token is not None
                claims = await verify_token(token)
                # Logged-out tokens are still cryptographically valid until they
                # expire; the denylist is what makes logout take effect at once.
                if is_access_token_revoked(claims["jti"]):
                    raise AuthenticationError("This session has been signed out", code="TOKEN_REVOKED")
                cognito_sub = claims["sub"]
            ctx = await _load_profile_and_scope(cognito_sub, request_id=request_id, ip_address=caller_ip)
        except AnavaException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            )
        except PoolTimeoutError:
            # Raised here it never reaches main.py's exception handlers
            # (those sit inside this middleware), so answer it directly.
            logger.warning("db_pool_timeout", path=request.url.path, stage="auth_context")
            return server_busy_response()

        if not ctx.is_active:
            allowed_prefixes = PATIENT_SELF_REGISTRATION_PATH_PREFIXES if ctx.role == "patient" else CONSENT_FLOW_PATH_PREFIXES
            if not request.url.path.startswith(allowed_prefixes):
                return JSONResponse(
                    status_code=403,
                    content={"error": {"code": "CONSENT_REQUIRED", "message": "Sign your onboarding consent to continue", "details": None}},
                )

        set_request_context(ctx)
        # For ApiAuditMiddleware (outside this one): a contextvar set here never
        # flows back out to it, request.state (the shared scope) does.
        request.state.ctx = ctx
        return await call_next(request)


# ─── API audit recorder (perf/api-audit) ──────────────────────────────────
# Off unless settings.api_audit (env API_AUDIT=1): main.py only registers
# ApiAuditMiddleware + the query-count listener when it is on. Writes one JSON
# line per request to settings.api_audit_log for the API audit, which joins
# it to a Playwright browser log by X-Request-ID. Pure ASGI (not
# BaseHTTPMiddleware) so a streaming/SSE body passes through untouched —
# only JSON bodies are copied (capped) to read their shape. A logging failure
# is swallowed; it must never break or alter the request.

_audit_db_queries: ContextVar[list[int] | None] = ContextVar("_audit_db_queries", default=None)

# Matched as substrings of the lower-cased key, so "new_password", "otp_code",
# "refresh_token", "razorpay_signature" are all caught. Over-redacting a
# "pincode" is the accepted cost of never writing an OTP.
_AUDIT_SECRET_KEYS = ("password", "passwd", "otp", "code", "token", "secret", "authorization", "cookie", "ticket", "signature")
_AUDIT_TAGS = ("portal", "chapter", "flow", "step", "action", "run")
_AUDIT_LIST_KEYS = ("items", "data", "results", "rows", "records")
_AUDIT_BODY_LIMIT = 2 * 1024 * 1024  # ponytail: bigger bodies are sized, not parsed


def count_audit_db_query(*_args) -> None:
    """before_cursor_execute listener on engine.sync_engine. The counter list
    is created per request by ApiAuditMiddleware; SQLAlchemy runs the sync
    cursor in a greenlet that inherits the request's contextvars, so every
    query of that request (including AuthContextMiddleware's scope lookup)
    lands in the same list. Queries outside a request (workers) see None."""
    counter = _audit_db_queries.get()
    if counter is not None:
        counter[0] += 1


def _audit_redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if any(s in str(k).lower() for s in _AUDIT_SECRET_KEYS) else _audit_redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_audit_redact(v) for v in value]
    return value


def _audit_query(qs: str) -> dict:
    return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(qs, keep_blank_values=True).items()}


def _audit_body(raw: bytes, content_type: str):
    if not raw:
        return None
    media = content_type.split(";")[0].strip()
    if len(raw) < _AUDIT_BODY_LIMIT:
        if "json" in media:
            try:
                return _audit_redact(json.loads(raw))
            except ValueError:
                pass
        elif media == "application/x-www-form-urlencoded":
            return _audit_redact(_audit_query(raw.decode("utf-8", "replace")))
    return f"<{len(raw)} bytes {media or 'unknown'}>"


def _audit_shape(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    items, list_key = None, None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        list_key = next((k for k in _AUDIT_LIST_KEYS if isinstance(data.get(k), list)), None)
        items = data[list_key] if list_key else None
    return {
        "response_keys": list(data.keys()) if isinstance(data, dict) else None,
        "list_key": list_key,
        "item_count": len(items) if items is not None else None,
        "first_item_keys": list(items[0].keys()) if items and isinstance(items[0], dict) else None,
    }


class ApiAuditMiddleware:
    def __init__(self, app: ASGIApp, log_path: str) -> None:
        self.app = app
        self.log_path = log_path
        with contextlib.suppress(Exception):
            os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def _write(self, record: dict) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            logger.warning("api_audit_write_failed", exc_info=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or path == "/health" or path.startswith("/health/"):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        # Every response must carry an id the browser log can join on. The
        # auth middleware's early 401/403 returns never reach
        # RequestIDMiddleware, so the id is fixed here, passed downstream as
        # if the client sent it (AuthContextMiddleware and
        # RequestIDMiddleware both honour an incoming X-Request-ID), and put
        # on the response below when missing.
        request_id = headers.get("x-request-id")
        if not request_id:
            request_id = str(uuid.uuid4())
            scope["headers"] = [*scope["headers"], (b"x-request-id", request_id.encode("latin-1"))]

        counter = [0]
        counter_token = _audit_db_queries.set(counter)
        req_body = bytearray()
        resp_body = bytearray()
        resp: dict = {"status": None, "bytes": 0, "content_type": "", "sse": False}

        def base_record() -> dict:
            ctx = scope.get("state", {}).get("ctx")
            return {
                "ts": ts,
                "request_id": request_id,
                "method": scope["method"],
                # FastAPI >=0.13x no longer copies included routes with their
                # prefix: scope["route"].path is "/refresh" for
                # /api/v1/auth/refresh. The effective route context holds the
                # full template; top-level app routes only have scope["route"].
                "route_template": getattr((scope.get("fastapi") or {}).get("effective_route_context"), "path", None)
                or getattr(scope.get("route"), "path", None),
                "raw_path": path,
                "query_params": _audit_redact(_audit_query(scope.get("query_string", b"").decode("latin-1"))),
                "status": resp["status"],
                "user_id": getattr(ctx, "user_id", None),
                "role": getattr(ctx, "role", None),
                "audit": {t: headers.get(f"x-audit-{t}") for t in _AUDIT_TAGS},
            }

        async def receive_wrapper() -> Message:
            message = await receive()
            if message["type"] == "http.request" and len(req_body) <= _AUDIT_BODY_LIMIT:
                req_body.extend(message.get("body", b""))
            return message

        async def send_wrapper(message: Message) -> None:
            try:
                if message["type"] == "http.response.start":
                    resp["status"] = message["status"]
                    raw_headers = list(message.get("headers", []))
                    resp_headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in raw_headers}
                    resp["content_type"] = resp_headers.get("content-type", "")
                    if "x-request-id" not in resp_headers:
                        message = {**message, "headers": [*raw_headers, (b"x-request-id", request_id.encode("latin-1"))]}
                    if resp["content_type"].startswith("text/event-stream"):
                        # The body never ends while the user stays on the
                        # page — log the open now, the close in finally.
                        resp["sse"] = True
                        self._write({**base_record(), "event": "stream_open", "db_query_count": counter[0]})
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    resp["bytes"] += len(body)
                    if "json" in resp["content_type"] and len(resp_body) <= _AUDIT_BODY_LIMIT:
                        resp_body.extend(body)
            except Exception:
                logger.warning("api_audit_capture_failed", exc_info=True)
            await send(message)

        error: str | None = None
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            _audit_db_queries.reset(counter_token)
            try:
                record = base_record()
                record.update(
                    event="stream_close" if resp["sse"] else "request",
                    status=resp["status"] or (500 if error else None),
                    request_content_type=headers.get("content-type"),
                    # request_body is teed as the app reads it; a body nobody
                    # read (early 401, unused) is null, this still sizes it.
                    request_content_length=headers.get("content-length"),
                    request_body=_audit_body(bytes(req_body), headers.get("content-type", "")),
                    response_content_type=resp["content_type"],
                    response_bytes=resp["bytes"],
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                    db_query_count=counter[0],
                    error=error,
                )
                if "json" in resp["content_type"] and len(resp_body) <= _AUDIT_BODY_LIMIT:
                    record.update(_audit_shape(bytes(resp_body)))
                self._write(record)
            except Exception:
                logger.warning("api_audit_record_failed", exc_info=True)
