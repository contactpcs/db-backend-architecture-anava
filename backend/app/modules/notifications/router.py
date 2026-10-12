import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core import live
from app.core.auth_session import issue_stream_ticket
from app.core.db import RequestContext, engine, get_db
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.core.permissions import get_current_context, require_role
from app.core.security import verify_token
from app.modules.notifications import schemas as s
from app.modules.notifications.service import NotificationService

logger = structlog.get_logger()

router = APIRouter()


@router.get("/notifications", response_model=list[s.NotificationRead])
async def list_notifications(unread_only: bool = False, db=Depends(get_db), ctx: RequestContext = Depends(get_current_context)):
    return await NotificationService(db).list_for_user(UUID(ctx.user_id), unread_only=unread_only)


@router.get("/notifications/unread-count")
async def get_unread_count(db=Depends(get_db), ctx: RequestContext = Depends(get_current_context)):
    return {"unread_count": await NotificationService(db).unread_count(UUID(ctx.user_id))}


@router.patch("/notifications/read")
async def mark_notifications_read(body: s.MarkReadRequest, db=Depends(get_db), ctx: RequestContext = Depends(get_current_context)):
    count = await NotificationService(db).mark_read(UUID(ctx.user_id), body.notification_ids)
    return {"marked_read": count}


@router.post("/events/ticket")
async def create_stream_ticket(request: Request, _ctx: RequestContext = Depends(get_current_context)):
    """Mints the one-time ticket the browser opens GET /events/stream with.

    EventSource cannot send an Authorization header, so it used to carry the
    access token in the URL — where it ends up in access logs, proxy logs and
    browser history. A ticket is single-use and expires in seconds, so a
    leaked one is already worthless. Called with the normal Bearer token
    (get_current_context has already verified it)."""
    claims = await verify_token(request.headers["Authorization"].removeprefix("Bearer ").strip())
    try:
        ticket = await issue_stream_ticket(claims["sub"])
    except AuthenticationError:
        raise
    except Exception as exc:
        # 503 tells the client to try again later rather than to log out.
        logger.warning("stream_ticket_unavailable", error=str(exc))
        raise ExternalServiceError("Live updates are temporarily unavailable", code="STREAM_UNAVAILABLE") from exc
    return {"ticket": ticket}


@router.get("/events/stream")
async def event_stream(ctx: RequestContext = Depends(get_current_context)):
    """SSE live feed (Architecture Section 25.1) — one connection per logged-in
    user, fed by this process's Postgres listener (core/live.py).
    Authenticated either by a Bearer token or, for a browser EventSource
    (which cannot set headers), by the one-time ?ticket= from
    POST /events/ticket — see AuthContextMiddleware."""

    async def generator():
        with live.subscribe(ctx.user_id) as queue:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    yield ": ping\n\n"  # keepalive — ALB/proxy idle-timeout guard (Section 25.1)
                    continue
                if message is None:
                    # The listener reconnected, so pushes may have been missed.
                    # Ending the stream makes the browser reconnect and reload.
                    return
                yield f"data: {message}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        # A proxy that buffers (nginx, some CDNs) holds SSE frames until its
        # buffer fills — the stream "connects" but no popup ever arrives.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/health/live")
async def live_pipeline_health(_ctx: RequestContext = Depends(require_role("super_admin"))):
    """Diagnoses the live-notification chain step by step:

        action -> outbox event -> relay -> notifications row -> NOTIFY -> listener -> SSE -> popup

    Checks this API process only: each one has its own relay and listener.
    Super-admin only: the output names infrastructure and raw error strings."""
    import time
    import uuid as _uuid

    from app.config import get_settings
    from app.workers.event_relay import RELAY_STATE, relay_backlog

    settings = get_settings()
    report: dict = {}

    # 1. Relay alive, and can it see the outbox?
    relay: dict = {"enabled": settings.event_relay_enabled, **RELAY_STATE}
    for k in ("started_at", "last_drain_at", "last_event_at"):
        if relay.get(k):
            relay[k + "_seconds_ago"] = round(time.time() - relay.pop(k), 1)
    try:
        relay["backlog"] = await relay_backlog()
    except Exception as exc:
        relay["backlog"] = {"error": repr(exc)[:300]}
    relay["uses_migration_login"] = bool(settings.migration_database_url)
    report["relay"] = relay

    # 2. Listener connected, and does a NOTIFY come back through it to a
    # stream? Sent to a made-up user id, so no real stream receives it.
    connected_at = live.LISTENER_STATE["connected_at"]
    listener: dict = {"connected": connected_at is not None, "last_error": live.LISTENER_STATE["last_error"]}
    if connected_at:
        listener["connected_seconds_ago"] = round(time.time() - connected_at, 1)
        probe = f"health-{_uuid.uuid4().hex}"
        with live.subscribe(probe) as queue:
            try:
                t = time.monotonic()
                async with engine.begin() as conn:
                    await conn.execute(
                        text("SELECT pg_notify(:channel, :payload)"),
                        {"channel": live.USER_STREAM, "payload": f"{probe} ping"},
                    )
                await asyncio.wait_for(queue.get(), timeout=5)
                listener["roundtrip_ms"] = round((time.monotonic() - t) * 1000, 1)
            except Exception as exc:
                listener["roundtrip_error"] = repr(exc)[:300]
    report["listener"] = listener

    # 3. Verdict — the first broken link, in chain order.
    backlog = relay["backlog"]
    if not settings.event_relay_enabled:
        verdict = "Relay is switched off (EVENT_RELAY_ENABLED=false) — no notifications are created."
    elif "error" in backlog:
        verdict = "Relay cannot read the outbox — apply SQL/v1/93 and 102, and check the relay's DB login."
    elif relay.get("last_drain_at_seconds_ago") is None or relay["last_drain_at_seconds_ago"] > 60:
        verdict = "Relay is not draining (no drain in the last minute) — see relay.last_error."
    elif backlog.get("undelivered", 0) > 50:
        verdict = "Relay is falling behind — events are piling up undelivered."
    elif not listener["connected"]:
        verdict = (
            "Listener is not connected — notifications are saved (bell) but no live popups. "
            "See listener.last_error; DATABASE_URL must be a direct endpoint, not RDS Proxy."
        )
    elif "roundtrip_ms" not in listener:
        verdict = "Listener is connected but a NOTIFY did not come back — live popups cannot be delivered."
    else:
        verdict = (
            "Backend live chain OK. If popups still don't appear, check the browser: "
            "POST /events/ticket and GET /events/stream in the Network tab."
        )
    if backlog.get("failed"):
        verdict += f" Also: {backlog['failed']} event(s) gave up after retries — see last_error in ops.outbox_events."
    report["verdict"] = verdict
    return report
