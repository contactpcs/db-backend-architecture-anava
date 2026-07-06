import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import build_ssl_context, get_settings

config = context.config
settings = get_settings()
# Migrations need DDL privileges (CREATE/ALTER/DROP) — the RDS master user
# in real environments, not the scoped anava_app runtime role the app itself
# connects as (see config.py). Falls back to database_url when unset, which
# is always the case for local Docker dev (one role does everything there).
config.set_main_option("sqlalchemy.url", settings.migration_database_url or settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No SQLAlchemy ORM metadata target yet — schema is defined in SQL/*.sql and
# applied via raw-SQL migrations (see versions/0001_baseline_schema.py).
# Per-module models.py files (added as each module is built) will extend
# this once autogenerate support is needed for incremental schema changes.
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connect_args = {}
    ssl_context = build_ssl_context()
    if ssl_context is not None:
        connect_args["ssl"] = ssl_context

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
