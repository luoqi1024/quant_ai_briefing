from src.business_calendar import is_daily_push_day, is_china_workday, weekly_summary_window


def test_is_daily_push_day_for_normal_workday():
    assert is_daily_push_day("2026-06-16") is True


def test_is_daily_push_day_rejects_statutory_holiday_weekday():
    assert is_china_workday("2026-05-01") is False
    assert is_daily_push_day("2026-05-01") is False


def test_is_daily_push_day_rejects_weekend_even_if_manual_check():
    assert is_daily_push_day("2026-06-14") is False


def test_weekly_summary_window_uses_ended_monday_to_friday():
    window = weekly_summary_window("2026-06-21")

    assert window.anchor_date.isoformat() == "2026-06-21"
    assert window.week_start.isoformat() == "2026-06-15"
    assert window.week_end.isoformat() == "2026-06-19"
