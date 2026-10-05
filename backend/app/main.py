import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.config import get_settings
from app.core import perf_probe
from app.core.db import RequestContext, engine
from app.core.exceptions import AnavaException
from app.core.live import run_listener_forever
from app.core.middleware import (
    ApiAuditMiddleware,
    AuthContextMiddleware,
    RequestIDMiddleware,
    assert_auth_context_function,
    count_audit_db_query,
    server_busy_response,
)
from app.core.permissions import require_role
from app.core.security import warm_jwks
from app.modules.admin.router import router as admin_router
from app.modules.anamnesis.router import router as anamnesis_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.clinical.router import router as clinical_router
from app.modules.consent.router import router as consent_router
from app.modules.device_sessions.router import router as device_sessions_router
from app.modules.files.router import router as files_router
from app.modules.inventory.router import router as inventory_router
from app.modules.notifications.router import router as notifications_router
from app.modules.patients.router import router as patients_router
from app.modules.payments.router import router as payments_router
from app.modules.prs.router import router as prs_router
from app.modules.reception.router import router as reception_router
from app.modules.reports.router import router as reports_router
from app.modules.scheduling.router import router as scheduling_router
from app.modules.staff.router import router as staff_router
from app.modules.store.router import router as store_router
from app.modules.treatment_protocols.router import router as treatment_protocols_router
from app.workers.event_relay import run_forever as run_event_relay_forever
from app.workers.hold_sweeper import run_hold_sweeper_forever
from app.workers.no_show_sweeper import run_no_show_sweeper_forever
from app.workers.retention_purge import run_partition_maintenance_forever

settings = get_settings()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Background jobs, both running in-process on every API instance. Each
    takes a Postgres advisory lock internally, which makes that safe with
    multiple uvicorn workers and multiple instances — so neither needs a
    separate worker deployment.

    partition maintenance  keeps monthly/yearly partitions created ahead of the
                           current date so inserts never fall through to the
                           DEFAULT partition (SQL/v1/07_tables_compliance.sql).

    hold sweeper           releases expired appointment holds, so an abandoned
                           checkout gives its slot back instead of blocking it
                           forever (SQL/v1/31_appointments_payment_states.sql).
                           Finds nothing while appointment_payment_required is
                           False — wired now so enabling payment is one config
                           flag, not a deployment.

    no-show sweeper        auto-marks an unattended appointment 'no_show'
                           instead of leaving it stuck at 'paid'/'checked_in'
                           forever with nobody noticing (app/workers/
                           no_show_sweeper.py).

    event relay            turns outbox events into notifications + live SSE
                           pushes. Claims each event with FOR UPDATE SKIP
                           LOCKED instead of an advisory lock, so several
                           instances share the queue without double-sending.

    live listener          this process's one Postgres LISTEN connection:
                           delivers those pushes to its open SSE streams and
                           keeps its copy of the logged-out tokens current
                           (app/core/live.py). Needs no lock: every process
                           must hear every message.
    """
    # Auth needs ops.auth_context (alembic 0054). Missing -> refuse to boot.
    # DB unreachable -> only warn; requests will surface it, and a DB blip at
    # deploy time must not keep the API down.
    try:
        await assert_auth_context_function()
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("auth_context_check_skipped", error=repr(exc))

    tasks: list[asyncio.Task] = []
    if settings.partition_maintenance_enabled:
        tasks.append(asyncio.create_task(run_partition_maintenance_forever()))
    if settings.appointment_hold_sweeper_enabled:
        tasks.append(asyncio.create_task(run_hold_sweeper_forever()))
    if settings.appointment_no_show_sweeper_enabled:
        tasks.append(asyncio.create_task(run_no_show_sweeper_forever()))
    tasks.append(asyncio.create_task(run_listener_forever()))
    tasks.append(asyncio.create_task(warm_jwks()))
    if settings.event_relay_enabled:
        tasks.append(asyncio.create_task(run_event_relay_forever()))
    if settings.perf_probe:
        tasks.append(asyncio.create_task(perf_probe.run_sampler(engine, settings.perf_probe_dir)))
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


_docs_on = settings.api_docs_enabled if settings.api_docs_enabled is not None else settings.environment == "local"
# Production must not publish the full API map (every route, parameter and
# schema) to anonymous callers — docs are served only when enabled.
app = FastAPI(
    title="Anava Clinic Backend",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

# Starlette wraps middleware in reverse add-order (last added = outermost),
# so CORSMiddleware must be added LAST — otherwise AuthContextMiddleware
# intercepts the OPTIONS preflight first, returns 401 (no route is public),
# and the browser never sees an Access-Control-Allow-Origin header.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(AuthContextMiddleware)
if settings.api_audit:
    # Outside AuthContextMiddleware so its early 401/403s are recorded too,
    # inside CORS for the reason above (preflights are skipped anyway).
    event.listen(engine.sync_engine, "before_cursor_execute", count_audit_db_query)
    app.add_middleware(ApiAuditMiddleware, log_path=settings.api_audit_log)
if settings.perf_probe:
    # Outside everything except CORS, so its total is the whole request.
    perf_probe.install(engine)
    app.add_middleware(perf_probe.PerfProbeMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AnavaException)
async def anava_exception_handler(request: Request, exc: AnavaException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.exception_handler(PoolTimeoutError)
async def pool_timeout_handler(request: Request, exc: PoolTimeoutError) -> JSONResponse:
    """An endpoint waited db_pool_timeout_seconds for a database connection and
    got none: overload. Without this it surfaced as a bare 500."""
    logger.warning("db_pool_timeout", path=request.url.path, stage="endpoint")
    return server_busy_response()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Safety net so a stray `raise HTTPException(...)` anywhere (framework
    validation, a module that forgets to use the AnavaException hierarchy)
    still returns the standard {"error": {...}} envelope instead of
    Starlette's default {"detail": ...} shape."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail), "details": []}},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness — process is up. No dependency checks, no auth."""
    return {"status": "ok"}


@app.get("/health/version")
async def health_version() -> dict[str, int]:
    """What the deploy workflow checks after a release: how many API paths
    this build serves — the same number /openapi.json's "paths" gave, which
    production no longer exposes. A bare count, nothing sensitive."""
    # Built in-process (and cached by FastAPI) even though the URL is off.
    return {"paths": len(app.openapi()["paths"])}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    """Readiness — can this instance actually serve traffic."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready"}


app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
app.include_router(staff_router, prefix="/api/v1", tags=["staff"])
app.include_router(consent_router, prefix="/api/v1", tags=["consent"])
app.include_router(anamnesis_router, prefix="/api/v1", tags=["anamnesis"])
app.include_router(prs_router, prefix="/api/v1", tags=["prs"])
app.include_router(patients_router, prefix="/api/v1", tags=["patients"])
app.include_router(files_router, prefix="/api/v1", tags=["files"])
app.include_router(clinical_router, prefix="/api/v1", tags=["clinical"])
app.include_router(scheduling_router, prefix="/api/v1", tags=["scheduling"])
app.include_router(treatment_protocols_router, prefix="/api/v1", tags=["treatment-protocols"])
app.include_router(device_sessions_router, prefix="/api/v1", tags=["device-sessions"])
app.include_router(payments_router, prefix="/api/v1", tags=["payments"])
app.include_router(store_router, prefix="/api/v1", tags=["store"])
app.include_router(inventory_router, prefix="/api/v1", tags=["inventory"])
app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])
app.include_router(reception_router, prefix="/api/v1/reception", tags=["reception"])
app.include_router(reports_router, prefix="/api/v1", tags=["reports"])
app.include_router(audit_router, prefix="/api/v1", tags=["audit"])


@app.get("/api/v1/_internal/whoami")
async def whoami(ctx: RequestContext = Depends(require_role("super_admin"))) -> dict[str, str | None]:
    """Foundation smoke-test endpoint — proves auth + role permission + RLS
    context resolution work end to end. Remove once a real module exposes
    an equivalent authenticated endpoint (e.g. admin module's own routes)."""
    return {"user_id": ctx.user_id, "role": ctx.role, "clinic_id": ctx.clinic_id, "region_id": ctx.region_id}
