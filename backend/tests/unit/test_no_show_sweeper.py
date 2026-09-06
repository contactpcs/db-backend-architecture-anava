"""Config-level lock for the no-show sweeper's grace windows — the actual
SQL sweep (workers/no_show_sweeper.py) needs a real database and is verified
separately against a live instance, same as hold_sweeper's own SQL."""

from app.config import get_settings


def test_paid_grace_window_is_two_hours():
    assert get_settings().appointment_no_show_paid_grace_hours == 2.0


def test_checked_in_grace_window_is_six_hours():
    assert get_settings().appointment_no_show_checked_in_grace_hours == 6.0


def test_sweeper_enabled_by_default():
    assert get_settings().appointment_no_show_sweeper_enabled is True
