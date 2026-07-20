"""Regression test for app/core/fsm.py — extracted during the eng review
from three copy-pasted status-transition guards (store, inventory, admin)."""

import pytest

from app.core.exceptions import BusinessRuleError
from app.core.fsm import assert_transition

_TRANSITIONS = {"pending": {"dispatched"}, "dispatched": {"received"}, "received": set()}


def test_assert_transition_allows_valid_move():
    assert_transition("pending", "dispatched", _TRANSITIONS, entity="thing", code="INVALID")  # no raise


def test_assert_transition_blocks_invalid_move():
    with pytest.raises(BusinessRuleError) as exc_info:
        assert_transition("pending", "received", _TRANSITIONS, entity="thing", code="INVALID_THING_TRANSITION")
    assert exc_info.value.code == "INVALID_THING_TRANSITION"


def test_assert_transition_blocks_move_from_terminal_state():
    with pytest.raises(BusinessRuleError):
        assert_transition("received", "pending", _TRANSITIONS, entity="thing", code="INVALID")


def test_assert_transition_blocks_unknown_current_state():
    with pytest.raises(BusinessRuleError):
        assert_transition("unknown_state", "pending", _TRANSITIONS, entity="thing", code="INVALID")
