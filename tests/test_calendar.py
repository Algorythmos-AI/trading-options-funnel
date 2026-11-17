from datetime import UTC

import pytest

from options_funnel import calendar


def test_the_pinned_calendar_loads() -> None:
    cal = calendar.load()
    assert cal.is_session("2026-11-18") and not cal.is_session("2026-11-26") and not cal.is_session("2026-11-21")


def test_previous_close_after_a_half_day_and_across_daylight_saving() -> None:
    cal = calendar.load()
    after_half_day = cal.previous_close("2026-11-30")
    assert after_half_day.isoformat() == "2026-11-27T13:00:00-05:00"
    after_dst = cal.previous_close("2026-11-02")
    assert after_dst.astimezone(UTC).isoformat() == "2026-10-30T20:00:00+00:00"


def test_session_plus_skips_holidays() -> None:
    cal = calendar.load()
    assert cal.session_plus("2026-11-24", 4) == "2026-12-01"  # Thanksgiving closed, 27 Nov a half day
    assert cal.session_plus("2026-11-24", 0) == "2026-11-24"
    with pytest.raises(calendar.CalendarError):
        cal.session_plus("2026-11-26", 1)


def test_dates_outside_the_calendar_are_refused() -> None:
    with pytest.raises(calendar.CalendarError):
        calendar.load().is_session("2031-01-02")
