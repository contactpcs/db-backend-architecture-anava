"""Outbox writer — every module's service layer calls emit_event() as part of
its normal DB transaction (same session, no separate commit). The relay
(app/workers/event_relay.py) picks up unpublished rows and publishes to SQS.
See SQL/17_outbox_events.sql and Architecture ADR-006 / Section 25.2.

Also the sole writer of compliance.activity_logs — the architecture doc
(Anava_Backend_Architecture_v1.md section 6) always meant this one helper to
feed both tables ("activity_logs (app-written) ... core/events.py provides
the write-side helper every module calls"), but only the outbox_events half
was ever built. See SQL/v1/89_activity_logs_writer.sql."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_request_context

# aggregate_type -> activity_logs.category. New aggregate_types default to
# "other" rather than raising — this is a compliance/reporting grouping, not
# something worth failing a business transaction over.
_CATEGORY_BY_AGGREGATE = {
    "appointment": "scheduling",
    "clinic_device": "scheduling",
    "clinic_device_schedule": "scheduling",
    "payment": "billing",
    "device_session": "clinical",
    "treatment_protocol": "clinical",
    "protocol_instance": "clinical",
    "protocol_custom_montage": "clinical",
    "prs_assessment_instance": "clinical",
    "anamnesis_assessment": "clinical",
    "patient": "patient",
    "patient_clinic_transfer": "patient",
    "patient_file": "patient",
    "consent_record": "patient",
    "doctor": "staff",
    "clinical_assistant": "staff",
    "receptionist": "staff",
    "staff_request": "staff",
    "clinic_staff_assignment": "staff",
    "clinic": "admin",
    "clinic_request": "admin",
    "device_assignment": "inventory",
    "stock_transfer": "inventory",
    "store_order": "inventory",
}


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

    # activity_logs projection — see module docstring. entity_id is UUID-typed
    # but not every aggregate_id is a real UUID (e.g. prs_assessment_instance
    # uses a short TEXT id) — falls back to NULL rather than failing the
    # whole write over what's a nice-to-have join, not a correctness need.
    try:
        entity_id: uuid.UUID | None = uuid.UUID(str(aggregate_id))
    except ValueError:
        entity_id = None

    ctx = get_request_context()
    await session.execute(
        text(
            "INSERT INTO activity_logs "
            "(actor_id, actor_role, request_id, category, event_type, entity_type, entity_id, "
            " clinic_id, region_id, metadata, ip_address) "
            "VALUES (:actor_id, :actor_role, :request_id, :category, :event_type, :entity_type, :entity_id, "
            " :clinic_id, :region_id, CAST(:metadata AS JSONB), CAST(:ip_address AS INET))"
        ),
        {
            "actor_id": ctx.user_id if ctx else None,
            "actor_role": ctx.role if ctx else (actor_role or "system"),
            "request_id": ctx.request_id if ctx else None,
            "category": _CATEGORY_BY_AGGREGATE.get(aggregate_type, "other"),
            "event_type": event_type,
            "entity_type": aggregate_type,
            "entity_id": entity_id,
            "clinic_id": ctx.clinic_id if ctx else None,
            "region_id": ctx.region_id if ctx else None,
            "metadata": json.dumps(payload),
            "ip_address": ctx.ip_address if ctx else None,
        },
    )
