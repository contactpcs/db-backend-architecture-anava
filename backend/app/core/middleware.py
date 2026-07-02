import time
import uuid

import structlog
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.db import RequestContext, engine, set_request_context
from app.core.exceptions import AnavaException, PermissionError_
from app.core.security import verify_token

logger = structlog.get_logger()

# Paths that never require auth. Kept short and explicit rather than a regex —
# an accidentally-too-broad pattern here is a real security bug.
PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/v1/auth/login",
    "/api/v1/auth/local-login",  # dev-only (Stage 13 removes this route entirely)
    "/api/v1/webhooks/razorpay",  # authenticated via HMAC signature, not a user JWT
}


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
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


async def _load_profile_and_scope(cognito_sub: str) -> RequestContext:
    """Resolves the caller's profile + tenant scope. Deliberately minimal
    (raw parameterized SQL, not an ORM model) — the `profiles`/`admins`/
    `clinic_staff_assignments`/`patients` tables already exist in the schema
    from Stage 2's migration even though their owning modules (admin/staff/
    patients) haven't been built yet. Extend this once those modules exist
    if a richer scope lookup is needed; don't duplicate the query there."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, role, is_active FROM profiles WHERE cognito_sub = :sub"),
                {"sub": cognito_sub},
            )
        ).first()
        if row is None or not row.is_active:
            raise PermissionError_("Profile not found or inactive", code="PROFILE_NOT_FOUND")

        profile_id, role = str(row.id), row.role
        clinic_id: str | None = None
        region_id: str | None = None

        if role in ("super_admin", "regional_admin", "clinic_admin"):
            scope = (
                await conn.execute(
                    text("SELECT region_id, clinic_id FROM admins WHERE profile_id = :pid"),
                    {"pid": profile_id},
                )
            ).first()
            if scope:
                region_id = str(scope.region_id) if scope.region_id else None
                clinic_id = str(scope.clinic_id) if scope.clinic_id else None
        elif role in ("doctor", "clinical_assistant", "receptionist"):
            scope = (
                await conn.execute(
                    text(
                        "SELECT clinic_id FROM clinic_staff_assignments "
                        "WHERE profile_id = :pid AND is_active = TRUE LIMIT 1"
                    ),
                    {"pid": profile_id},
                )
            ).first()
            if scope:
                clinic_id = str(scope.clinic_id)
        elif role == "patient":
            scope = (
                await conn.execute(
                    text("SELECT primary_clinic_id FROM patients WHERE profile_id = :pid"),
                    {"pid": profile_id},
                )
            ).first()
            if scope and scope.primary_clinic_id:
                clinic_id = str(scope.primary_clinic_id)

    return RequestContext(user_id=profile_id, role=role, clinic_id=clinic_id, region_id=region_id)


class AuthContextMiddleware(BaseHTTPMiddleware):
    """Validates the bearer token and resolves identity/tenant scope BEFORE
    any route/permission dependency runs. Sets the RequestContext contextvar
    that core/db.py's get_db() dependency applies via SET LOCAL for RLS."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "MISSING_TOKEN", "message": "Authorization header required"}},
            )
        token = auth_header.removeprefix("Bearer ").strip()

        try:
            claims = await verify_token(token)
            ctx = await _load_profile_and_scope(claims["sub"])
            set_request_context(ctx)
        except AnavaException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            )

        return await call_next(request)
