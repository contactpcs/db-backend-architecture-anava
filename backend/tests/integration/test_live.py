"""Stream tickets, logged-out tokens and the live listener against a real
database, connected as the app login."""

import asyncio
import time
import uuid

from app.core import auth_session as sess
from app.core import live
from tests.integration.conftest import connected_listener, needs_test_database

pytestmark = needs_test_database


async def test_stream_ticket_works_once_and_only_its_hash_is_stored(admin):
    ticket = await sess.issue_stream_ticket("cognito-sub-1")

    assert (await admin("SELECT count(*) FROM sse_tickets WHERE ticket_hash = :t", t=ticket)).scalar() == 0
    assert await sess.consume_stream_ticket(ticket) == "cognito-sub-1"
    assert await sess.consume_stream_ticket(ticket) is None


async def test_unknown_and_expired_stream_tickets_are_refused(admin):
    assert await sess.consume_stream_ticket("not-a-real-ticket") is None

    ticket = await sess.issue_stream_ticket("cognito-sub-1")
    await admin("UPDATE sse_tickets SET expires_at = now() - interval '1 second'")
    assert await sess.consume_stream_ticket(ticket) is None

    await sess.issue_stream_ticket("cognito-sub-2")  # issuing clears expired tickets
    assert (await admin("SELECT count(*) FROM sse_tickets WHERE cognito_sub = 'cognito-sub-1'")).scalar() == 0


async def test_logout_is_honoured_here_at_once_and_stored(admin):
    jti = uuid.uuid4().hex

    await sess.revoke_access_token(jti, int(time.time()) + 600)

    assert sess.is_access_token_revoked(jti)
    assert (await admin("SELECT count(*) FROM revoked_access_tokens WHERE jti = :jti", jti=jti)).scalar() == 1


async def test_logout_is_announced_to_every_process(listener, monkeypatch):
    jti, expires_at = uuid.uuid4().hex, int(time.time()) + 600
    marked = []
    monkeypatch.setattr(live, "mark_revoked", lambda *args: marked.append(args))

    await sess.revoke_access_token(jti, expires_at)
    await asyncio.sleep(0.5)

    # Once by the process handling the logout, once by the NOTIFY every process hears.
    assert marked == [(jti, expires_at), (jti, expires_at)]


async def test_listener_loads_logged_out_tokens_when_it_connects(admin):
    jti = uuid.uuid4().hex
    await admin("INSERT INTO revoked_access_tokens (jti, expires_at) VALUES (:jti, now() + interval '10 minutes')", jti=jti)

    async with connected_listener():
        assert sess.is_access_token_revoked(jti)


async def test_open_streams_are_ended_when_the_listener_reconnects():
    with live.subscribe("some-user") as queue:
        async with connected_listener():
            assert queue.get_nowait() is None
