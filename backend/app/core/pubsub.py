"""Redis pub/sub helper for the SSE live-notification feed (Architecture
Section 25.1). One channel per user: user:{user_id}:stream. The notification
worker publishes here as a side-effect of processing an outbox event; the SSE
endpoint subscribes here for the life of the connection."""

import redis.asyncio as aioredis

from app.config import get_settings

settings = get_settings()
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        # An unreachable Redis (VPC-only ElastiCache from a laptop, a network
        # blip) must fail in seconds, not after the OS connect timeout — this
        # client is reached from the per-request auth path and the relay. No
        # socket_timeout on purpose: the SSE subscriber legitimately blocks in
        # get_message(timeout=25). health_check_interval drops dead pooled
        # connections (idle timeouts, failovers) instead of reusing them.
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _redis


def user_channel(user_id: str) -> str:
    return f"user:{user_id}:stream"


async def publish_to_user(user_id: str, message: str) -> None:
    await get_redis().publish(user_channel(user_id), message)
