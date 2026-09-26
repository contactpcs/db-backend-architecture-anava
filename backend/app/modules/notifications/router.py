import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth_session import issue_stream_ticket
from app.core.db import RequestContext, get_db
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.core.permissions import get_current_context, require_role
from app.core.pubsub import get_redis, user_channel
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
        # No Redis = no live stream anyway (the relay publishes through it).
        # 503 tells the client to try again later rather than to log out.
        logger.warning("stream_ticket_unavailable", error=str(exc))
        raise ExternalServiceError("Live updates are temporarily unavailable", code="STREAM_UNAVAILABLE") from exc
    return {"ticket": ticket}


@router.get("/events/stream")
async def event_stream(ctx: RequestContext = Depends(get_current_context)):
    """SSE live feed (Architecture Section 25.1) — one connection per logged-in
    user, fed by the notification worker's Redis publish. Authenticated either
    by a Bearer token or, for a browser EventSource (which cannot set
    headers), by the one-time ?ticket= from POST /events/ticket — see
    AuthContextMiddleware."""

    async def generator():
        # Redis is required for live push, but its absence (not installed/
        # not running — a real gap in some local dev setups) must never
        # crash this connection. An unhandled exception here kills the
        # StreamingResponse, EventSource's browser-native auto-reconnect
        # fires instantly, and the client re-crashes the same way in a tight
        # loop — that's the actual symptom, not just a log nuisance. Once
        # Redis is confirmed unreachable, this connection degrades to a
        # plain keepalive-only stream (no live pushes, but stays open and
        # quiet) instead of retrying Redis on every message tick.
        redis = get_redis()
        pubsub = redis.pubsub()
        degraded = False
        try:
            await pubsub.subscribe(user_channel(ctx.user_id))
        except Exception:
            logger.warning("sse_redis_unavailable", user_id=ctx.user_id)
            degraded = True

        try:
            while True:
                if degraded:
                    yield ": ping\n\n"
                    await asyncio.sleep(25.0)
                    continue
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=25.0)
                except Exception:
                    logger.warning("sse_redis_lost", user_id=ctx.user_id)
                    degraded = True
                    continue
                if message is None:
                    yield ": ping\n\n"  # keepalive — ALB/proxy idle-timeout guard (Section 25.1)
                    continue
                yield f"data: {message['data']}\n\n"
        finally:
            if not degraded:
                try:
                    await pubsub.unsubscribe(user_channel(ctx.user_id))
                except Exception:
                    pass

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

        action -> outbox event -> relay -> notifications row -> Redis publish -> SSE -> popup

    Talks to Redis directly (not through the breaker) with its own short
    timeouts, so it reports the real current state. Super-admin only: the
    output names infrastructure and raw error strings."""
    import time
    import uuid as _uuid
    from urllib.parse import urlparse

    from app.config import get_settings
    from app.core.auth_session import breaker_open_for_seconds
    from app.workers.event_relay import RELAY_STATE, relay_backlog

    settings = get_settings()
    report: dict = {}

    # 1. Redis reachable?
    redis_url = urlparse(settings.redis_url)
    redis_info: dict = {"host": redis_url.hostname, "port": redis_url.port, "tls": redis_url.scheme == "rediss"}
    redis = get_redis()
    try:
        t = time.monotonic()
        await asyncio.wait_for(redis.ping(), timeout=5)
        redis_info["ping_ms"] = round((time.monotonic() - t) * 1000, 1)
        redis_info["ok"] = True
    except Exception as exc:
        redis_info["ok"] = False
        redis_info["error"] = repr(exc)[:300]
    redis_info["breaker_open_for_seconds"] = round(breaker_open_for_seconds(), 1)

    # 2. Pub/sub round trip on a private channel (the relay -> SSE path).
    if redis_info["ok"]:
        channel = f"health:live:{_uuid.uuid4().hex[:8]}"
        pubsub = redis.pubsub()
        try:
            await asyncio.wait_for(pubsub.subscribe(channel), timeout=5)
            await redis.publish(channel, "ping")
            msg = None
            for _ in range(10):
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if msg:
                    break
            redis_info["pubsub_roundtrip"] = bool(msg)
        except Exception as exc:
            redis_info["pubsub_roundtrip"] = False
            redis_info["pubsub_error"] = repr(exc)[:300]
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass
    report["redis"] = redis_info

    # 3. Relay alive, and can it see the outbox?
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

    # 4. Verdict — the first broken link, in chain order.
    backlog = relay["backlog"]
    if not settings.event_relay_enabled:
        verdict = "Relay is switched off (EVENT_RELAY_ENABLED=false) — no notifications are created."
    elif "error" in backlog:
        verdict = "Relay cannot read the outbox — apply SQL/v1/93 and check the relay's DB login."
    elif relay.get("last_drain_at_seconds_ago") is None or relay["last_drain_at_seconds_ago"] > 60:
        verdict = "Relay is not draining (no drain in the last minute) — see relay.last_error."
    elif backlog.get("undelivered", 0) > 50:
        verdict = "Relay is falling behind — events are piling up undelivered."
    elif not redis_info["ok"]:
        verdict = "Redis unreachable — notifications are saved (bell) but no live popups. Check the ElastiCache security group / VPC."
    elif not redis_info.get("pubsub_roundtrip"):
        verdict = "Redis reachable but pub/sub failed — live popups cannot be delivered."
    else:
        verdict = (
            "Backend live chain OK. If popups still don't appear, check the browser: "
            "POST /events/ticket and GET /events/stream in the Network tab."
        )
    report["verdict"] = verdict
    return report
