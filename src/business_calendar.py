"""Business-calendar helpers for report scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from cn_workdays import calculate_working_date


@dataclass(frozen=True)
class WeeklySummaryWindow:
    anchor_date: date
    week_start: date
    week_end: date


def as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_china_workday(value: str | date) -> bool:
    target = as_date(value)
    return calculate_working_date(target, 0) == target


def is_daily_push_day(value: str | date) -> bool:
    target = as_date(value)
    if target.isoweekday() > 5:
        return False
    return is_china_workday(target)


def weekly_summary_window(value: str | date) -> WeeklySummaryWindow:
    target = as_date(value)
    anchor = target - timedelta(days=target.isoweekday() % 7)
    week_start = anchor - timedelta(days=6)
    week_end = anchor - timedelta(days=2)
    return WeeklySummaryWindow(
        anchor_date=anchor,
        week_start=week_start,
        week_end=week_end,
    )
