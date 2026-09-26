"""Refresh cookie packing, logged-out-token denylist, one-time stream tickets.

No Redis and no event-loop fixtures: a tiny in-memory stand-in implements the
handful of commands core/auth_session.py uses, and the coroutines are driven
with asyncio.run.
"""

import asyncio
import time

import pytest

from app.core import auth_session as sess


class FakeRedis:
    def __init__(self):
        self.data: dict[str, tuple[str, float | None]] = {}

    def _live(self, key):
        item = self.data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires <= time.time():
            del self.data[key]
            return None
        return value

    async def set(self, key, value, ex=None):
        self.data[key] = (value, time.time() + ex if ex else None)

    async def exists(self, key):
        return 1 if self._live(key) is not None else 0

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, key):
        self.ops.append(("get", key))

    def delete(self, key):
        self.ops.append(("delete", key))

    async def execute(self):
        out = []
        for op, key in self.ops:
            if op == "get":
                out.append(self.redis._live(key))
            else:
                out.append(1 if self.redis.data.pop(key, None) else 0)
        return out


class BrokenRedis:
    """Every command fails — what a Redis outage looks like from here."""

    async def set(self, *a, **k):
        raise ConnectionError("redis down")

    async def exists(self, *a, **k):
        raise ConnectionError("redis down")

    def pipeline(self, transaction=True):
        raise ConnectionError("redis down")


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    """The breaker is module state; one test's outage must not leak into the next."""
    monkeypatch.setattr(sess, "_down_until", 0.0)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sess, "get_redis", lambda: fake)
    return fake


# ── refresh cookie ───────────────────────────────────────────────────────────


def test_refresh_cookie_round_trip():
    packed = sess.pack_refresh_cookie("user-123", "eyJ.refresh.token")
    assert sess.unpack_refresh_cookie(packed) == ("user-123", "eyJ.refresh.token")


@pytest.mark.parametrize("bad", [None, "", "no-separator", "|token-only", "user-only|"])
def test_refresh_cookie_rejects_malformed_values(bad):
    assert sess.unpack_refresh_cookie(bad) is None


# ── logged-out access tokens ─────────────────────────────────────────────────


def test_revoked_access_token_is_rejected_and_others_are_not(redis):
    async def scenario():
        await sess.revoke_access_token("jti-1", int(time.time()) + 600)
        return await sess.is_access_token_revoked("jti-1"), await sess.is_access_token_revoked("jti-2")

    revoked, other = asyncio.run(scenario())
    assert revoked is True
    assert other is False


def test_token_without_a_jti_is_never_treated_as_revoked(redis):
    assert asyncio.run(sess.is_access_token_revoked(None)) is False


def test_revocation_check_fails_open_when_redis_is_down(monkeypatch):
    """A Redis outage must not lock every user out of the app."""
    monkeypatch.setattr(sess, "get_redis", lambda: BrokenRedis())
    assert asyncio.run(sess.is_access_token_revoked("jti-1")) is False


def test_logout_still_succeeds_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(sess, "get_redis", lambda: BrokenRedis())
    asyncio.run(sess.revoke_access_token("jti-1", int(time.time()) + 600))  # must not raise


# ── stream tickets ───────────────────────────────────────────────────────────


def test_stream_ticket_works_once_then_is_dead(redis):
    async def scenario():
        ticket = await sess.issue_stream_ticket("cognito-sub-1")
        return await sess.consume_stream_ticket(ticket), await sess.consume_stream_ticket(ticket)

    first, second = asyncio.run(scenario())
    assert first == "cognito-sub-1"
    assert second is None


def test_unknown_stream_ticket_is_refused(redis):
    assert asyncio.run(sess.consume_stream_ticket("not-a-real-ticket")) is None


def test_expired_stream_ticket_is_refused(redis):
    async def scenario():
        ticket = await sess.issue_stream_ticket("cognito-sub-1")
        key = sess._TICKET_PREFIX + ticket
        value, _ = redis.data[key]
        redis.data[key] = (value, time.time() - 1)
        return await sess.consume_stream_ticket(ticket)

    assert asyncio.run(scenario()) is None


def test_stream_ticket_is_refused_when_redis_is_down(monkeypatch):
    """A ticket that cannot be verified is a ticket that is refused."""
    monkeypatch.setattr(sess, "get_redis", lambda: BrokenRedis())
    assert asyncio.run(sess.consume_stream_ticket("anything")) is None


def test_issuing_a_ticket_raises_when_redis_is_down(monkeypatch):
    """The endpoint turns this into a 503 the client retries, not a logout."""
    monkeypatch.setattr(sess, "get_redis", lambda: BrokenRedis())
    with pytest.raises(ConnectionError):
        asyncio.run(sess.issue_stream_ticket("cognito-sub-1"))


def test_breaker_skips_redis_after_one_failure(monkeypatch):
    """With Redis unreachable, only the first request pays the timeout; the
    ones behind it skip Redis instantly instead of each waiting in turn."""
    calls = {"n": 0}

    class CountingBroken(BrokenRedis):
        async def exists(self, *a, **k):
            calls["n"] += 1
            raise ConnectionError("redis down")

    monkeypatch.setattr(sess, "get_redis", lambda: CountingBroken())
    for _ in range(5):
        assert asyncio.run(sess.is_access_token_revoked("jti-1")) is False
    assert calls["n"] == 1


def test_slow_redis_is_cut_off_by_the_hard_timeout(monkeypatch):
    class Slow(FakeRedis):
        async def exists(self, key):
            await asyncio.sleep(10)
            return 1

    monkeypatch.setattr(sess, "get_redis", lambda: Slow())
    monkeypatch.setattr(sess, "_OP_TIMEOUT_SECONDS", 0.2)
    t = time.time()
    assert asyncio.run(sess.is_access_token_revoked("jti-1")) is False
    assert time.time() - t < 2
