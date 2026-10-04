"""The outbox relay against a real database, connected as the app login
(RLS role 'system') exactly as it runs in the API: notifications get written,
a failed event is retried without duplicating, and a hopeless one is parked.

Needs the SQL/v1 schema, so it runs only where ENVIRONMENT=test (CI) — never
against the database a developer's .env points at."""

import json
import uuid

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core.db import get_migration_engine
from app.workers import event_relay

pytestmark = pytest.mark.skipif(get_settings().environment != "test", reason="needs the CI test database")

EVENT_TYPE = "test.relay"


class Relay:
    """Arranges and inspects rows over the admin connection (bypasses RLS)."""

    def __init__(self, engine, recipient: str):
        self.engine, self.recipient = engine, recipient
        self.broken: set[str] = set()
        self.pushed: list[str] = []

    async def _run(self, sql: str, **params):
        async with self.engine.begin() as conn:
            return await conn.execute(text(sql), params)

    async def add_event(self, title: str) -> str:
        outbox_id = str(uuid.uuid4())
        await self._run(
            "INSERT INTO outbox_events (outbox_id, aggregate_type, aggregate_id, event_type, payload) "
            "VALUES (:id, 'test', :aggregate_id, :type, CAST(:payload AS JSONB))",
            id=outbox_id,
            aggregate_id=outbox_id,
            type=EVENT_TYPE,
            payload=json.dumps({"title": title}),
        )
        return outbox_id

    async def event(self, outbox_id: str):
        return (await self._run("SELECT * FROM outbox_events WHERE outbox_id = :id", id=outbox_id)).mappings().one()

    async def titles(self) -> list[str]:
        rows = await self._run("SELECT title FROM notifications WHERE recipient_id = :r ORDER BY title", r=self.recipient)
        return [row.title for row in rows]

    async def drain_due(self) -> None:
        """Skips the retry delay, then drains."""
        await self._run("UPDATE outbox_events SET next_attempt_at = now() WHERE event_type = :type", type=EVENT_TYPE)
        await event_relay.drain_outbox()

    async def handler(self, session, payload: dict) -> list[dict]:
        notes = [{"recipient_id": self.recipient, "type": "system", "title": payload["title"]}]
        if payload["title"] in self.broken:
            # No such profile: the insert fails AFTER the first row was written.
            notes.append({**notes[0], "recipient_id": str(uuid.uuid4())})
        return notes

    async def publish(self, recipient_id: str, message: str) -> None:
        self.pushed.append(json.loads(message)["title"])


@pytest.fixture
async def relay(monkeypatch):
    engine = get_migration_engine()
    recipient = str(uuid.uuid4())
    relay = Relay(engine, recipient)
    await relay._run(
        "INSERT INTO profiles (id, cognito_sub, email, first_name, last_name, role) VALUES (:id, :sub, :email, 'Relay', 'Test', 'doctor')",
        id=recipient,
        sub=f"test-{recipient}",
        email=f"{recipient}@example.com",
    )
    monkeypatch.setitem(event_relay.EVENT_HANDLERS, EVENT_TYPE, relay.handler)
    monkeypatch.setattr(event_relay, "publish_to_user", relay.publish)
    yield relay
    await relay._run("DELETE FROM notifications WHERE recipient_id = :r", r=recipient)
    await relay._run("DELETE FROM outbox_events WHERE event_type = :type", type=EVENT_TYPE)
    await relay._run("DELETE FROM profiles WHERE id = :r", r=recipient)
    await engine.dispose()
    # The relay's pool is created at import time; its connections belong to
    # this test's event loop and must not be reused by the next one.
    await event_relay._relay_engine.dispose()


async def test_event_becomes_one_notification_and_one_push(relay):
    outbox_id = await relay.add_event("booked")

    await event_relay.drain_outbox()

    assert await relay.titles() == ["booked"]
    assert relay.pushed == ["booked"]
    assert (await relay.event(outbox_id))["published_at"] is not None


async def test_failed_event_is_retried_without_duplicates_and_does_not_block_the_queue(relay):
    relay.broken.add("flaky")
    flaky = await relay.add_event("flaky")
    behind = await relay.add_event("behind")

    await event_relay.drain_outbox()

    event = await relay.event(flaky)
    assert event["published_at"] is None and event["failed_at"] is None
    assert event["publish_attempts"] == 1 and event["last_error"]
    assert event["next_attempt_at"] > event["created_at"]
    assert (await relay.event(behind))["published_at"] is not None
    assert await relay.titles() == ["behind"]  # the half-written "flaky" row was rolled back
    assert relay.pushed == ["behind"]  # and nothing was pushed for it

    await event_relay.drain_outbox()  # not due yet: left alone
    assert (await relay.event(flaky))["publish_attempts"] == 1

    relay.broken.clear()
    await relay.drain_due()

    assert (await relay.event(flaky))["published_at"] is not None
    assert await relay.titles() == ["behind", "flaky"]


async def test_event_is_parked_after_the_last_attempt(relay):
    relay.broken.add("hopeless")
    outbox_id = await relay.add_event("hopeless")

    for _ in range(event_relay.MAX_ATTEMPTS + 1):
        await relay.drain_due()

    event = await relay.event(outbox_id)
    assert event["failed_at"] is not None and event["published_at"] is None
    assert event["publish_attempts"] == event_relay.MAX_ATTEMPTS  # the extra drain did not touch it
    assert await relay.titles() == []
    assert (await event_relay.relay_backlog())["failed"] >= 1
