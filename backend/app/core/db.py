from collections.abc import AsyncGenerator
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


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


_request_context: ContextVar[RequestContext | None] = ContextVar("_request_context", default=None)


def set_request_context(ctx: RequestContext) -> None:
    _request_context.set(ctx)


def get_request_context() -> RequestContext | None:
    return _request_context.get()


async def _apply_rls_context(session: AsyncSession) -> None:
    ctx = get_request_context()
    if ctx is None:
        return
    await session.execute(
        text_set_local("app.current_user_id", ctx.user_id)
    )
    await session.execute(text_set_local("app.current_user_role", ctx.role))
    if ctx.clinic_id:
        await session.execute(text_set_local("app.current_clinic_id", ctx.clinic_id))
    if ctx.region_id:
        await session.execute(text_set_local("app.current_region_id", ctx.region_id))


def text_set_local(setting_name: str, value: str):
    from sqlalchemy import text

    # set_config(..., true) = LOCAL (transaction-scoped), matches 15_rls_policies.sql assumption
    return text("SELECT set_config(:name, :value, true)").bindparams(name=setting_name, value=value)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one transaction per request. RLS context is applied
    inside the same transaction as every query the handler makes."""
    async with async_session_factory() as session:
        async with session.begin():
            await _apply_rls_context(session)
            yield session
