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
) -> None:
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
