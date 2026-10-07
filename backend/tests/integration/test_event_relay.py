"""The outbox relay against a real database, connected as the app login
(RLS role 'system') exactly as it runs in the API: notifications get written
and pushed live, a failed event is retried without duplicating, and a hopeless
one is parked."""

import asyncio
import json
import uuid

import pytest

from app.core import live
from app.workers import event_relay
from tests.integration.conftest import needs_test_database

pytestmark = needs_test_database

EVENT_TYPE = "test.relay"


class Relay:
    def __init__(self, admin, recipient: str, queue: asyncio.Queue):
        self.admin, self.recipient, self.queue = admin, recipient, queue
        self.broken: set[str] = set()

    async def add_event(self, title: str) -> str:
        outbox_id = str(uuid.uuid4())
        await self.admin(
            "INSERT INTO outbox_events (outbox_id, aggregate_type, aggregate_id, event_type, payload) "
            "VALUES (:id, 'test', :aggregate_id, :type, CAST(:payload AS JSONB))",
            id=outbox_id,
            aggregate_id=outbox_id,
            type=EVENT_TYPE,
            payload=json.dumps({"title": title}),
        )
        return outbox_id

    async def event(self, outbox_id: str):
        return (await self.admin("SELECT * FROM outbox_events WHERE outbox_id = :id", id=outbox_id)).mappings().one()

    async def titles(self) -> list[str]:
        rows = await self.admin("SELECT title FROM notifications WHERE recipient_id = :r ORDER BY title", r=self.recipient)
        return [row.title for row in rows]

    async def pushed(self) -> list[str]:
        """Titles pushed to the recipient's open stream since the last call."""
        await asyncio.sleep(0.5)  # a NOTIFY arrives a few ms after its commit
        titles = []
        while not self.queue.empty():
            titles.append(json.loads(self.queue.get_nowait())["title"])
        return titles

    async def drain_due(self) -> None:
        """Skips the retry delay, then drains."""
        await self.admin("UPDATE outbox_events SET next_attempt_at = now() WHERE event_type = :type", type=EVENT_TYPE)
        await event_relay.drain_outbox()

    async def handler(self, session, payload: dict) -> list[dict]:
        notes = [{"recipient_id": self.recipient, "type": "system", "title": payload["title"]}]
        if payload["title"] in self.broken:
            # No such profile: the insert fails AFTER the first row was written.
            notes.append({**notes[0], "recipient_id": str(uuid.uuid4())})
        return notes


@pytest.fixture
async def relay(admin, listener, monkeypatch):
    recipient = str(uuid.uuid4())
    await admin(
        "INSERT INTO profiles (id, cognito_sub, email, first_name, last_name, role) VALUES (:id, :sub, :email, 'Relay', 'Test', 'doctor')",
        id=recipient,
        sub=f"test-{recipient}",
        email=f"{recipient}@example.com",
    )
    with live.subscribe(recipient) as queue:
        relay = Relay(admin, recipient, queue)
        monkeypatch.setitem(event_relay.EVENT_HANDLERS, EVENT_TYPE, relay.handler)
        yield relay
    await admin("DELETE FROM notifications WHERE recipient_id = :r", r=recipient)
    await admin("DELETE FROM outbox_events WHERE event_type = :type", type=EVENT_TYPE)
    await admin("DELETE FROM profiles WHERE id = :r", r=recipient)


async def test_event_becomes_one_notification_and_one_push(relay):
    outbox_id = await relay.add_event("booked")

    await event_relay.drain_outbox()

    assert await relay.titles() == ["booked"]
    assert await relay.pushed() == ["booked"]
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
    assert await relay.pushed() == ["behind"]  # and so was its push

    await event_relay.drain_outbox()  # not due yet: left alone
    assert (await relay.event(flaky))["publish_attempts"] == 1

    relay.broken.clear()
    await relay.drain_due()

    assert (await relay.event(flaky))["published_at"] is not None
    assert await relay.titles() == ["behind", "flaky"]
    assert await relay.pushed() == ["flaky"]


async def test_event_is_parked_after_the_last_attempt(relay):
    relay.broken.add("hopeless")
    outbox_id = await relay.add_event("hopeless")

    for _ in range(event_relay.MAX_ATTEMPTS + 1):
        await relay.drain_due()

    event = await relay.event(outbox_id)
    assert event["failed_at"] is not None and event["published_at"] is None
    assert event["publish_attempts"] == event_relay.MAX_ATTEMPTS  # the extra drain did not touch it
    assert await relay.titles() == []
    assert await relay.pushed() == []
    assert (await event_relay.relay_backlog())["failed"] >= 1
