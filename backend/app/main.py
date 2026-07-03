import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.core.db import RequestContext, engine
from app.core.exceptions import AnavaException
from app.core.middleware import AuthContextMiddleware, RequestIDMiddleware
from app.core.permissions import require_role
from app.modules.admin.router import router as admin_router
from app.modules.anamnesis.router import router as anamnesis_router
from app.modules.auth.router import router as auth_router
from app.modules.clinical.router import router as clinical_router
from app.modules.consent.router import router as consent_router
from app.modules.files.router import router as files_router
from app.modules.inventory.router import router as inventory_router
from app.modules.notifications.router import router as notifications_router
from app.modules.patients.router import router as patients_router
from app.modules.payments.router import router as payments_router
from app.modules.prs.router import router as prs_router
from app.modules.scheduling.router import router as scheduling_router
from app.modules.staff.router import router as staff_router
from app.modules.store.router import router as store_router

settings = get_settings()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)

app = FastAPI(title="Anava Clinic Backend", version="0.1.0")

# Starlette wraps middleware in reverse add-order (last added = outermost),
# so CORSMiddleware must be added LAST — otherwise AuthContextMiddleware
# intercepts the OPTIONS preflight first, returns 401 (no route is public),
# and the browser never sees an Access-Control-Allow-Origin header.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(AuthContextMiddleware)
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


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness — process is up. No dependency checks, no auth."""
    return {"status": "ok"}


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
app.include_router(payments_router, prefix="/api/v1", tags=["payments"])
app.include_router(store_router, prefix="/api/v1", tags=["store"])
app.include_router(inventory_router, prefix="/api/v1", tags=["inventory"])
app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])


@app.get("/api/v1/_internal/whoami")
async def whoami(ctx: RequestContext = Depends(require_role("super_admin"))) -> dict[str, str | None]:
    """Foundation smoke-test endpoint — proves auth + role permission + RLS
    context resolution work end to end. Remove once a real module exposes
    an equivalent authenticated endpoint (e.g. admin module's own routes)."""
    return {"user_id": ctx.user_id, "role": ctx.role, "clinic_id": ctx.clinic_id, "region_id": ctx.region_id}
