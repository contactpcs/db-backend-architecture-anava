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
    "/api/v1/auth/login/new-password",  # completes NEW_PASSWORD_REQUIRED — no session yet either
    "/api/v1/auth/local-login",  # dev-only (Stage 13 removes this route entirely)
    "/api/v1/auth/register",  # public patient self-registration — see patients module
    "/api/v1/auth/clinics",  # public clinic picker for the self-registration form
    "/api/v1/auth/config",  # public — tells the frontend which auth endpoints to call
    "/api/v1/auth/patients/signup/start",
    "/api/v1/auth/patients/signup/resend",
    "/api/v1/auth/patients/signup/verify",
    "/api/v1/auth/patients/signup/complete",
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
)


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
        # Must run before the SELECT below, in the same transaction — this is
        # what rls_profiles_select's self-lookup clause matches against, since
        # app.current_user_id/role can't be set yet (this query is what
        # determines them). See SQL/31_fix_profile_bootstrap_lookup_rls.sql.
        await conn.execute(text("SELECT set_config('app.current_cognito_sub', :sub, true)"), {"sub": cognito_sub})
        row = (
            await conn.execute(
                text("SELECT id, role, is_active, consent_signed FROM profiles WHERE cognito_sub = :sub"),
                {"sub": cognito_sub},
            )
        ).first()
        if row is None:
            raise PermissionError_("Profile not found", code="PROFILE_NOT_FOUND")

        profile_id, role = str(row.id), row.role
        is_active, consent_signed = row.is_active, row.consent_signed

        # Now genuinely known (resolved above) — set them immediately so
        # every query below this point on this same connection (self-heal's
        # consent_records check, the admins/clinic_staff_assignments/patients
        # scope lookups) can satisfy their own RLS self-lookup clauses
        # (profile_id/staff_id = rls_user_id()) instead of hitting the same
        # bootstrap chicken-and-egg problem the cognito_sub fix above solves
        # for the first query. See SQL/31_fix_profile_bootstrap_lookup_rls.sql.
        await conn.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": profile_id})
        await conn.execute(text("SELECT set_config('app.current_user_role', :role, true)"), {"role": role})

        # Self-heal: for staff roles, is_active is meant to mirror a signed
        # staff_onboarding consent record exactly (see
        # SQL/28_consent_redesign.sql / consent/service.py::sign). If it's
        # somehow FALSE despite a signed record already existing — a gap in
        # some future creation/signing path, the same class of bug that once
        # bricked a regional_admin here — re-derive from the real source of
        # truth (consent_records) instead of leaving the account stuck.
        # Scoped to staff only: patients have their own richer activation
        # gate (registration-complete/approval) that must NOT be
        # short-circuited by this check.
        #
        # Also requires consent_signed to STILL be FALSE — once an account
        # has ever been properly activated (consent_signed=TRUE), is_active
        # going FALSE afterward is a deliberate admin deactivation (staff
        # deactivate button, staff/service.py::_split_profile_fields), not a
        # bricked account — self-healing that back to TRUE would make
        # deactivation impossible. Real bricked accounts have BOTH flags
        # stuck FALSE despite a signed record existing; a deactivated one has
        # only is_active FALSE with consent_signed still TRUE.
        if role != "patient" and not is_active and not consent_signed:
            healed = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM consent_records WHERE staff_id = :pid "
                        "AND consent_type = 'staff_onboarding' AND status = 'signed' LIMIT 1"
                    ),
                    {"pid": profile_id},
                )
            ).first()
            if healed:
                async with engine.begin() as heal_conn:
                    await heal_conn.execute(
                        text("UPDATE profiles SET is_active = TRUE, consent_signed = TRUE WHERE id = :pid"),
                        {"pid": profile_id},
                    )
                is_active, consent_signed = True, True

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

    return RequestContext(
        user_id=profile_id, role=role, clinic_id=clinic_id, region_id=region_id,
        is_active=is_active, consent_signed=consent_signed,
    )


class AuthContextMiddleware(BaseHTTPMiddleware):
    """Validates the bearer token and resolves identity/tenant scope BEFORE
    any route/permission dependency runs. Sets the RequestContext contextvar
    that core/db.py's get_db() dependency applies via SET LOCAL for RLS."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
        elif request.url.path == "/api/v1/events/stream" and request.query_params.get("token"):
            # Browser EventSource can't set custom headers — SSE is the one
            # endpoint that accepts the token as a query param instead. Not
            # opened up generally: query-param tokens are more exposure-prone
            # (logs, browser history), so this stays scoped to just this path.
            token = request.query_params["token"]
        else:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "MISSING_TOKEN", "message": "Authorization header required"}},
            )

        try:
            claims = await verify_token(token)
            ctx = await _load_profile_and_scope(claims["sub"])
        except AnavaException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            )

        if not ctx.is_active:
            allowed_prefixes = PATIENT_SELF_REGISTRATION_PATH_PREFIXES if ctx.role == "patient" else CONSENT_FLOW_PATH_PREFIXES
            if not request.url.path.startswith(allowed_prefixes):
                return JSONResponse(
                    status_code=403,
                    content={"error": {"code": "CONSENT_REQUIRED", "message": "Sign your onboarding consent to continue", "details": None}},
                )

        set_request_context(ctx)
        return await call_next(request)
