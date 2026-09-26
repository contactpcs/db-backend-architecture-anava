import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth_session import issue_stream_ticket
from app.core.db import RequestContext, get_db
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.core.permissions import get_current_context
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

    return StreamingResponse(generator(), media_type="text/event-stream")
