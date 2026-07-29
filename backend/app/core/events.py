"""Outbox writer — every module's service layer calls emit_event() as part of
its normal DB transaction (same session, no separate commit). The relay
(app/workers/event_relay.py) picks up unpublished rows and publishes to SQS.
See SQL/17_outbox_events.sql and Architecture ADR-006 / Section 25.2."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def emit_event(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str | uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    actor_role: str | None = None,
) -> None:
    # outbox_events' own INSERT policy requires rls_user_role() IS NOT NULL —
    # true for every authenticated request (middleware already set it), but
    # NOT for a genuinely anonymous bootstrap flow (patient self-registration,
    # PUBLIC_PATHS — no JWT, no RequestContext, role never set at all).
    # Those callers know their own actor's role by the time they reach this
    # point (the entity they're describing an event about already exists —
    # you can't emit an event for a row that isn't created yet), so they pass
    # it here rather than every future anonymous flow needing to remember its
    # own SET LOCAL incantation. The guard never overrides an already-set
    # (real, authenticated) role — this only ever fills in a gap, never
    # overwrites a genuine identity.
    if actor_role:
        await session.execute(
            text(
                "SELECT set_config('app.current_user_role', :role, true) "
                "WHERE current_setting('app.current_user_role', true) IS NULL OR "
                "current_setting('app.current_user_role', true) = ''"
            ),
            {"role": actor_role},
        )
    await session.execute(
        text(
            "INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload) "
            "VALUES (:aggregate_type, :aggregate_id, :event_type, CAST(:payload AS JSONB))"
        ),
        {
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "event_type": event_type,
            "payload": json.dumps(payload),
        },
    )
