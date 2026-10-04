"""Refresh cookie packing, and what the logged-out-token denylist and stream
tickets do when the database cannot be reached. Their behaviour against a
real database is in tests/integration/test_live.py."""

import asyncio
import time
from contextlib import asynccontextmanager

import pytest

from app.core import auth_session as sess


@pytest.fixture
def database_down(monkeypatch):
    @asynccontextmanager
    async def unreachable():
        raise ConnectionError("database down")
        yield

    monkeypatch.setattr(sess, "_system_transaction", unreachable)


# ── refresh cookie ───────────────────────────────────────────────────────────


def test_refresh_cookie_round_trip():
    packed = sess.pack_refresh_cookie("user-123", "eyJ.refresh.token")
    assert sess.unpack_refresh_cookie(packed) == ("user-123", "eyJ.refresh.token")


@pytest.mark.parametrize("bad", [None, "", "no-separator", "|token-only", "user-only|"])
def test_refresh_cookie_rejects_malformed_values(bad):
    assert sess.unpack_refresh_cookie(bad) is None


# ── logged-out access tokens ─────────────────────────────────────────────────


def test_token_without_a_jti_is_never_treated_as_revoked():
    assert sess.is_access_token_revoked(None) is False


def test_logout_succeeds_and_is_honoured_here_when_the_database_is_down(database_down):
    asyncio.run(sess.revoke_access_token("jti-down", int(time.time()) + 600))  # must not raise
    assert sess.is_access_token_revoked("jti-down") is True


# ── stream tickets ───────────────────────────────────────────────────────────


def test_stream_ticket_is_refused_when_the_database_is_down(database_down):
    """A ticket that cannot be verified is a ticket that is refused."""
    assert asyncio.run(sess.consume_stream_ticket("anything")) is None


def test_issuing_a_ticket_raises_when_the_database_is_down(database_down):
    """The endpoint turns this into a 503 the client retries, not a logout."""
    with pytest.raises(ConnectionError):
        asyncio.run(sess.issue_stream_ticket("cognito-sub-1"))
