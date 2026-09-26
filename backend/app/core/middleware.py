import time
import uuid

import structlog
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.auth_session import consume_stream_ticket, is_access_token_revoked
from app.core.db import RequestContext, engine, set_request_context
from app.core.exceptions import AnavaException, AuthenticationError, PermissionError_
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
                    # profiles has RLS forced and this connection is fresh
                    # (no GUC carried over from the read above) — without
                    # this, rls_profiles_update admits nobody here and the
                    # UPDATE silently affects zero rows. Found live: this
                    # write-back had never actually been persisting — see
                    # SQL/v1/78_profiles_update_system_role_rls.sql.
                    await heal_conn.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
                    await heal_conn.execute(
                        text("UPDATE profiles SET is_active = TRUE, consent_signed = TRUE WHERE id = :pid"),
                        {"pid": profile_id},
                    )
                is_active, consent_signed = True, True

        # Mirror of the self-heal above, other direction: is_active = TRUE
        # must correspond to a REAL signed consent, not just a flag someone
        # (or a seed/bootstrap script) set directly. Found live: 6 staff/
        # admin accounts had is_active = TRUE and consent_signed = TRUE with
        # zero actual consent_records rows behind them — provisioned by a
        # seed script that set both flags directly, bypassing the sign flow
        # entirely. The super-admin consent view correctly reported "not
        # signed" for every one of them; the flags were simply wrong.
        # Re-verify against the real table on every request rather than
        # trusting the flag blindly, and write back (so this doesn't
        # re-query every request forever for a permanently-fine account) —
        # same "re-derive from the real source of truth" reasoning as the
        # self-heal block above, just checking the claim instead of
        # granting it. Costs one extra indexed lookup per staff request;
        # accepted deliberately — consent compliance is the kind of
        # correctness this app already pays for elsewhere (the whole
        # app-layer ownership-check pattern exists for the same reason).
        elif role != "patient" and is_active:
            really_signed = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM consent_records WHERE staff_id = :pid "
                        "AND consent_type = 'staff_onboarding' AND status = 'signed' LIMIT 1"
                    ),
                    {"pid": profile_id},
                )
            ).first()
            if not really_signed:
                async with engine.begin() as heal_conn:
                    await heal_conn.execute(text("SELECT set_config('app.current_user_role', 'system', true)"))
                    await heal_conn.execute(
                        text("UPDATE profiles SET is_active = FALSE, consent_signed = FALSE WHERE id = :pid"),
                        {"pid": profile_id},
                    )
                is_active, consent_signed = False, False

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
                    text("SELECT clinic_id FROM clinic_staff_assignments WHERE profile_id = :pid AND is_active = TRUE LIMIT 1"),
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

        # Clinic-closed / region-inactive lockout. super_admin/regional_admin
        # are exempt so someone can still log in to reopen/reactivate — same
        # reasoning as the pending_closure<->active revert path in
        # admin/service.py's clinic FSM. Checked via clinic_id's own region
        # (not admins.region_id directly) so clinic_admin/doctor/CA/
        # receptionist/patient are all covered through the one clinic_id they
        # already resolve to above.
        #
        # rls_clinics_select only admits a row when status NOT IN
        # (pending_closure, closed) OR clinic_id = rls_clinic_id() (and
        # rls_regions_select mirrors this with is_active = true OR region_id =
        # rls_region_id()) — app.current_clinic_id/current_region_id aren't
        # set yet at this point in the request (that happens later via
        # set_request_context), so a closed clinic / inactive region would
        # otherwise be invisible to its own query and this check would never
        # fire for the exact rows it exists to catch. Same self-lookup trap
        # as SQL/31_fix_profile_bootstrap_lookup_rls.sql — fixed the same way:
        # set the GUC to the specific row being checked, immediately before
        # checking it.
        if role not in ("super_admin", "regional_admin") and clinic_id:
            await conn.execute(text("SELECT set_config('app.current_clinic_id', :cid, true)"), {"cid": clinic_id})
            clinic_row = (
                await conn.execute(text("SELECT status, region_id FROM clinics WHERE clinic_id = :cid"), {"cid": clinic_id})
            ).first()
            if clinic_row:
                if clinic_row.status == "closed":
                    raise PermissionError_("This clinic is closed", code="CLINIC_CLOSED")
                if clinic_row.region_id:
                    region_id_str = str(clinic_row.region_id)
                    await conn.execute(text("SELECT set_config('app.current_region_id', :rid, true)"), {"rid": region_id_str})
                    region_row = (
                        await conn.execute(text("SELECT is_active FROM regions WHERE region_id = :rid"), {"rid": region_id_str})
                    ).first()
                    if region_row and not region_row.is_active:
                        raise PermissionError_("This region is inactive", code="REGION_INACTIVE")

    return RequestContext(
        user_id=profile_id,
        role=role,
        clinic_id=clinic_id,
        region_id=region_id,
        is_active=is_active,
        consent_signed=consent_signed,
        request_id=request_id,
        ip_address=ip_address,
    )


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
        client_ip = request.client.host if request.client else None

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
                if await is_access_token_revoked(claims["jti"]):
                    raise AuthenticationError("This session has been signed out", code="TOKEN_REVOKED")
                cognito_sub = claims["sub"]
            ctx = await _load_profile_and_scope(cognito_sub, request_id=request_id, ip_address=client_ip)
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
