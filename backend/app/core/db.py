from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import build_ssl_context, get_settings

settings = get_settings()

# asyncpg wants an ssl.SSLContext (or True/False), not libpq's sslmode string.
_connect_args: dict = {}
_ssl_context = build_ssl_context()
if _ssl_context is not None:
    _connect_args["ssl"] = _ssl_context

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def get_migration_engine() -> AsyncEngine:
    """For standalone scripts (bootstrap_superadmin.py, seed_*.py) — never
    import `engine` directly for a write to a table with an RLS INSERT policy
    (profiles, prs_diseases/scales/questions/options, admins, ...). Those
    policies require rls_user_role() = 'super_admin' (or similar), which is
    only ever set by AuthContextMiddleware inside a real HTTP request — a
    bare script has no such context. NOTE: as of this review, `engine`'s
    connecting role is NOT actually a scoped NOBYPASSRLS role in the
    deployed environment (see app/config.py's database_url comment and
    app/core/scoping.py's docstring) — it bypasses RLS entirely, so this
    INSERT would silently succeed rather than being rejected. Bootstrapping/seeding the very first
    data into a fresh system is inherently a privileged, one-time operation
    — use the master connection for it, the same one alembic uses for DDL.
    Discovered the hard way migrating to RDS: seed_dev_profile.py failed with
    InsufficientPrivilegeError until pointed at this instead of `engine`."""
    return create_async_engine(
        settings.migration_database_url or settings.database_url,
        connect_args=_connect_args,
    )


@dataclass(frozen=True)
class RequestContext:
    """Identity/tenant scope for the current request. Set once by auth middleware,
    applied to Postgres session state via SET LOCAL so RLS policies (15_rls_policies.sql)
    can read it through current_setting(). Never set as a plain SET (session-scoped) —
    SET LOCAL is transaction-scoped, which is required for pooled connections to not
    leak identity across requests."""

    user_id: str
    role: str
    clinic_id: str | None
    region_id: str | None
    is_active: bool = True
    consent_signed: bool = True
    request_id: str | None = None
    ip_address: str | None = None


_request_context: ContextVar[RequestContext | None] = ContextVar("_request_context", default=None)


def set_request_context(ctx: RequestContext) -> None:
    _request_context.set(ctx)


def get_request_context() -> RequestContext | None:
    return _request_context.get()


async def _apply_rls_context(session: AsyncSession) -> None:
    ctx = get_request_context()
    if ctx is None:
        return
    await session.execute(text_set_local("app.current_user_id", ctx.user_id))
    await session.execute(text_set_local("app.current_user_role", ctx.role))
    if ctx.clinic_id:
        await session.execute(text_set_local("app.current_clinic_id", ctx.clinic_id))
    if ctx.region_id:
        await session.execute(text_set_local("app.current_region_id", ctx.region_id))
    if ctx.request_id:
        await session.execute(text_set_local("app.request_id", ctx.request_id))
    if ctx.ip_address:
        await session.execute(text_set_local("app.client_ip", ctx.ip_address))


def text_set_local(setting_name: str, value: str):
    from sqlalchemy import text

    # set_config(..., true) = LOCAL (transaction-scoped), matches 15_rls_policies.sql assumption
    return text("SELECT set_config(:name, :value, true)").bindparams(name=setting_name, value=value)


@asynccontextmanager
async def as_system(session: AsyncSession) -> AsyncIterator[None]:
    """Run the enclosed writes under RLS role 'system', then restore the
    caller's role.

    For a write the app has already authorized but whose RLS policy only
    admits staff/system (e.g. rls_payments_update) while the request runs as
    a patient — without it the UPDATE silently matches 0 rows. Unlike a bare
    text_set_local(..., 'system'), the elevation ends with the block instead
    of lasting for the rest of the transaction."""
    from sqlalchemy import text

    previous = (await session.execute(text("SELECT current_setting('app.current_user_role', true)"))).scalar()
    await session.execute(text_set_local("app.current_user_role", "system"))
    try:
        yield
    finally:
        await session.execute(text_set_local("app.current_user_role", previous or ""))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one transaction per request. RLS context is applied
    inside the same transaction as every query the handler makes."""
    async with async_session_factory() as session:
        async with session.begin():
            await _apply_rls_context(session)
            yield session
