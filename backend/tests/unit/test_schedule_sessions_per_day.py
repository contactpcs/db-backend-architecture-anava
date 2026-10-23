"""ScheduleService.build with sessions_per_day (124): each treatment day holds
up to N device sessions and session_count stays the whole course. No DB needed."""

import datetime as dt
from collections import Counter

import pytest
from pydantic import ValidationError

from app.modules.treatment_protocols import schemas as s
from app.modules.treatment_protocols.service import ScheduleService

MONDAY = dt.date(2026, 10, 26)


def _build(**kw) -> dict:
    return ScheduleService.build(s.SchedulePreviewRequest(start_date=MONDAY, session_count=10, sessions_per_week=5, **kw))


def _per_day(preview: dict) -> Counter:
    return Counter(item["planned_date"] for item in preview["sessions"])


def test_default_is_one_session_per_day():
    preview = _build()
    assert preview["session_count"] == 10
    assert preview["day_count"] == 10
    assert set(_per_day(preview).values()) == {1}


def test_two_a_day_halves_the_treatment_days():
    preview = _build(sessions_per_day=2)
    assert preview["session_count"] == 10
    assert preview["day_count"] == 5
    assert set(_per_day(preview).values()) == {2}
    assert preview["last_date"] == MONDAY + dt.timedelta(days=4)
    assert [item["session_number"] for item in preview["sessions"]] == list(range(1, 11))


def test_last_day_holds_the_remainder():
    preview = _build(sessions_per_day=3)
    assert preview["session_count"] == 10
    assert sorted(_per_day(preview).values()) == [1, 3, 3, 3]


def test_extra_date_is_a_whole_treatment_day():
    saturday = MONDAY + dt.timedelta(days=5)
    preview = _build(sessions_per_day=2, extra_dates=[saturday])
    per_day = _per_day(preview)
    assert per_day[saturday] == 2
    assert preview["session_count"] == 10 and preview["day_count"] == 5


def test_follow_up_counts_sessions_not_days():
    preview = _build(sessions_per_day=2, follow_up_every_n=3)
    # Every 3rd session, the day after its treatment day: S3 (Tue) -> Wed, S6 (Wed) -> Thu, S9 (Fri) -> next Mon.
    assert preview["follow_ups"] == [
        {"after_session_number": 3, "planned_date": MONDAY + dt.timedelta(days=2)},
        {"after_session_number": 6, "planned_date": MONDAY + dt.timedelta(days=3)},
        {"after_session_number": 9, "planned_date": MONDAY + dt.timedelta(days=7)},
    ]


def test_follow_up_interval_is_limited_by_session_count():
    _build(sessions_per_day=2, follow_up_every_n=10)
    with pytest.raises(ValidationError):
        _build(sessions_per_day=2, follow_up_every_n=11)


@pytest.mark.parametrize("bad", [0, s.MAX_SESSIONS_PER_DAY + 1])
def test_daily_limit_outside_range_is_rejected(bad):
    with pytest.raises(ValidationError):
        _build(sessions_per_day=bad)
