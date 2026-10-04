"""The in-process half of app/core/live.py: routing a push to the right
streams, and the copy of the logged-out tokens. The listener itself needs a
database and is in tests/integration/test_live.py."""

import time

from app.core import live


def test_push_reaches_every_stream_of_its_user_and_nobody_else():
    with live.subscribe("user-1") as tab_a, live.subscribe("user-1") as tab_b, live.subscribe("user-2") as other:
        live._on_user_stream(None, 0, live.USER_STREAM, 'user-1 {"title": "a b"}')

        assert tab_a.get_nowait() == '{"title": "a b"}'
        assert tab_b.get_nowait() == '{"title": "a b"}'
        assert other.empty()


def test_closed_stream_is_forgotten():
    with live.subscribe("user-3"):
        assert "user-3" in live._streams
    assert "user-3" not in live._streams


def test_revoked_token_is_rejected_until_it_would_have_expired_anyway():
    live._on_token_revoked(None, 0, live.TOKEN_REVOKED, f"jti-live {int(time.time()) + 600}")
    live._on_token_revoked(None, 0, live.TOKEN_REVOKED, f"jti-lapsed {int(time.time()) - 1}")

    assert live.is_revoked("jti-live") is True
    assert live.is_revoked("jti-lapsed") is False
    assert live.is_revoked("jti-never-revoked") is False

    live._forget_expired_tokens()
    assert "jti-lapsed" not in live._revoked and "jti-live" in live._revoked
