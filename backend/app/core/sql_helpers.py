"""Small helper for the "build an INSERT from a dict of columns" pattern used
across every module's repository. Exists because asyncpg needs JSONB columns
explicitly serialized + cast — passing a raw Python dict/list as a bind param
fails with `'dict' object has no attribute 'encode'` (hit this first in
core/events.py's outbox writer, then again in staff_requests.candidate_credentials
— this helper is the fix applied once, reused everywhere, instead of
re-discovering the same bug in every module's repository)."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause


def _prepare(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Returns (bind_params, column->cast_expr overrides) — dict/list values
    are JSON-serialized and marked for an explicit ::JSONB cast in the SQL."""
    params: dict[str, Any] = {}
    casts: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            params[key] = json.dumps(value)
            casts[key] = "JSONB"
        else:
            params[key] = value
    return params, casts


def insert_returning(table: str, data: dict[str, Any]) -> tuple[TextClause, dict[str, Any]]:
    params, casts = _prepare(data)
    cols = list(data.keys())
    value_exprs = [f"CAST(:{c} AS {casts[c]})" if c in casts else f":{c}" for c in cols]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(value_exprs)}) RETURNING *"
    return text(sql), params


def update_returning(table: str, id_column: str, id_value: Any, data: dict[str, Any]) -> tuple[TextClause, dict[str, Any]]:
    params, casts = _prepare(data)
    set_exprs = [f"{c} = CAST(:{c} AS {casts[c]})" if c in casts else f"{c} = :{c}" for c in data]
    params["__id"] = id_value
    sql = f"UPDATE {table} SET {', '.join(set_exprs)} WHERE {id_column} = :__id RETURNING *"
    return text(sql), params


async def fetch_one(session: AsyncSession, sql: TextClause, params: dict[str, Any]) -> dict[str, Any]:
    row = (await session.execute(sql, params)).mappings().one()
    return dict(row)


async def fetch_optional(session: AsyncSession, sql: TextClause, params: dict[str, Any]) -> dict[str, Any] | None:
    row = (await session.execute(sql, params)).mappings().first()
    return dict(row) if row else None
