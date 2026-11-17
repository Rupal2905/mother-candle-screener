from datetime import date

from inside_screener.nse_calendar import NSETradingCalendar, OFFICIAL_HOLIDAYS, OFFICIAL_SPECIAL_SESSIONS


def test_official_2026_holidays_and_muhurat_override_are_classified_correctly():
    calendar = NSETradingCalendar(
        frozenset(day for days in OFFICIAL_HOLIDAYS.values() for day in days),
        frozenset(OFFICIAL_HOLIDAYS),
        dict(OFFICIAL_SPECIAL_SESSIONS),
    )
    assert calendar.expected_trading_day(date(2026, 1, 15)) is False
    assert calendar.expected_trading_day(date(2026, 10, 20)) is False
    assert calendar.expected_trading_day(date(2026, 11, 8)) is True
    assert calendar.close_time(date(2026, 11, 8)).isoformat() == "15:30:00"
