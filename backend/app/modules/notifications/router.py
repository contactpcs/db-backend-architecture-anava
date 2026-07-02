from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.db import RequestContext, get_db
from app.core.permissions import get_current_context
from app.core.pubsub import get_redis, user_channel
from app.modules.notifications import schemas as s
from app.modules.notifications.service import NotificationService

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


@router.get("/events/stream")
async def event_stream(ctx: RequestContext = Depends(get_current_context)):
    """SSE live feed (Architecture Section 25.1) — one connection per logged-in
    user, fed by the notification worker's Redis publish. Real browser clients
    would need a token-via-query-param variant since EventSource can't set
    Authorization headers; this dev/testable version keeps the same Bearer
    auth as every other endpoint, consistent with how it's tested here."""

    async def generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(user_channel(ctx.user_id))
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=25.0)
                if message is None:
                    yield ": ping\n\n"  # keepalive — ALB/proxy idle-timeout guard (Section 25.1)
                    continue
                yield f"data: {message['data']}\n\n"
        finally:
            await pubsub.unsubscribe(user_channel(ctx.user_id))

    return StreamingResponse(generator(), media_type="text/event-stream")
