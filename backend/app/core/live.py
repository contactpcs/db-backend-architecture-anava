"""Live channel between API processes, over Postgres LISTEN/NOTIFY
(design: Documents/design_postgres_notify_live.md).

Each API process holds ONE listening connection, however many users are
connected, for two channels:

  user_stream    a notification for one user's open SSE streams. Sent by the
                 outbox relay in the transaction that saves the notification,
                 so it is delivered only if that row was committed.
  token_revoked  an access token was logged out on some process.

A NOTIFY reaches only the processes listening at that moment. So each time the
listener (re)connects it reloads the revoked tokens from their table and ends
every open stream: the browser reconnects and reloads what it shows.
"""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import asyncpg
import structlog

from app.config import build_ssl_context, get_settings

logger = structlog.get_logger()
settings = get_settings()

USER_STREAM = "user_stream"  # payload: "<recipient_id> <message json>"
TOKEN_REVOKED = "token_revoked"  # payload: "<jti> <expiry, epoch seconds>"

# A LISTEN connection that died silently (dropped NAT entry, half-open socket)
# raises nothing on its own; the heartbeat query is what notices.
HEARTBEAT_SECONDS = 30.0
RECONNECT_SECONDS = 5.0

_streams: dict[str, set[asyncio.Queue[str | None]]] = {}  # user id -> open streams; None ends a stream
_revoked: dict[str, int] = {}  # jti -> expiry, epoch seconds

# For GET /api/v1/health/live.
LISTENER_STATE: dict[str, Any] = {"connected_at": None, "last_error": None}


@contextmanager
def subscribe(user_id: str) -> Iterator[asyncio.Queue[str | None]]:
    """The queue one SSE stream reads its user's pushes from."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    _streams.setdefault(user_id, set()).add(queue)
    try:
        yield queue
    finally:
        _streams[user_id].discard(queue)
        if not _streams[user_id]:
            del _streams[user_id]


def mark_revoked(jti: str, expires_at: int) -> None:
    _revoked[jti] = expires_at


def is_revoked(jti: str) -> bool:
    return _revoked.get(jti, 0) > time.time()


def _on_user_stream(_conn: Any, _pid: int, _channel: str, payload: str) -> None:
    user_id, _, message = payload.partition(" ")
    for queue in _streams.get(user_id, ()):
        queue.put_nowait(message)


def _on_token_revoked(_conn: Any, _pid: int, _channel: str, payload: str) -> None:
    jti, _, expires_at = payload.rpartition(" ")
    mark_revoked(jti, int(expires_at))


def _forget_expired_tokens() -> None:
    now = time.time()
    for jti in [jti for jti, expires_at in _revoked.items() if expires_at <= now]:
        del _revoked[jti]


async def _listen_until_lost() -> None:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn, ssl=build_ssl_context(), server_settings={"application_name": "anava-live-listener"})
    lost = asyncio.Event()
    conn.add_termination_listener(lambda _conn: lost.set())
    try:
        await conn.add_listener(USER_STREAM, _on_user_stream)
        await conn.add_listener(TOKEN_REVOKED, _on_token_revoked)
        # Listening first, loading second: a token revoked in between arrives
        # both ways instead of neither. Merged, never replaced, for the same reason.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_user_role', 'system', true)")
            rows = await conn.fetch(
                "SELECT jti, EXTRACT(EPOCH FROM expires_at)::bigint AS expires_at FROM revoked_access_tokens WHERE expires_at > now()"
            )
        _revoked.update((row["jti"], row["expires_at"]) for row in rows)
        for queues in _streams.values():
            for queue in queues:
                queue.put_nowait(None)
        LISTENER_STATE["connected_at"] = time.time()
        logger.info("live_listener_connected", revoked_tokens=len(_revoked))
        while True:
            try:
                await asyncio.wait_for(lost.wait(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                # Still open as far as this side can tell: prove it.
                await asyncio.wait_for(conn.execute("SELECT 1"), timeout=10)
                _forget_expired_tokens()
            else:
                raise ConnectionError("live listener connection was closed")
    finally:
        LISTENER_STATE["connected_at"] = None
        conn.terminate()


async def run_listener_forever() -> None:
    """Background loop started from the FastAPI lifespan (app/main.py)."""
    while True:
        try:
            await _listen_until_lost()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LISTENER_STATE["last_error"] = repr(exc)[:500]
            logger.warning("live_listener_lost_reconnecting", error=repr(exc))
            await asyncio.sleep(RECONNECT_SECONDS)
