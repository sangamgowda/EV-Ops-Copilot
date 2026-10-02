"""Relative dates, resolved in code.

"This week", "last month" and "this quarter" are given to Plan as
explicit date ranges, computed from config/app_config.yaml `calendar`
in the configured time zone. The model copies a range; it never works
out which day a week starts or which months make a quarter.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ops_copilot.settings import get_config

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def today() -> date:
    return datetime.now(ZoneInfo(get_config()["calendar"]["timezone"])).date()


def _month_start(d: date, months_back: int = 0) -> date:
    m = d.year * 12 + d.month - 1 - months_back
    return date(m // 12, m % 12 + 1, 1)


def ranges(on: date | None = None) -> dict[str, tuple[date, date]]:
    """Inclusive (first, last) day for each relative period."""
    cfg = get_config()["calendar"]
    d = on or today()

    if cfg["this_week"] == "rolling_7_days":
        this_week = (d - timedelta(days=6), d)
        last_week = (d - timedelta(days=13), d - timedelta(days=7))
    else:
        start = d - timedelta(days=(d.weekday() - _WEEKDAYS.index(cfg["week_starts_on"])) % 7)
        this_week = (start, d)
        last_week = (start - timedelta(days=7), start - timedelta(days=1))

    # Quarters count from the year's first month (1 = calendar year).
    offset = (d.month - cfg["year_start_month"]) % 12
    q_start = _month_start(d, offset % 3)
    year_start = _month_start(d, offset)

    return {
        "today": (d, d),
        "this week": this_week,
        "last week": last_week,
        "this month": (_month_start(d), d),
        "last month": (_month_start(d, 1), _month_start(d) - timedelta(days=1)),
        "this quarter": (q_start, d),
        "last quarter": (_month_start(q_start, 3), q_start - timedelta(days=1)),
        "this year": (year_start, d),
    }


def quarter_label(d: date) -> str:
    """Q1..Q4 of the configured year, named by the year it starts in."""
    start_month = get_config()["calendar"]["year_start_month"]
    offset = (d.month - start_month) % 12
    year = d.year if d.month >= start_month else d.year - 1
    return f"Q{offset // 3 + 1} {year}" if start_month == 1 else f"Q{offset // 3 + 1} FY{year}"


def calendar_block(on: date | None = None) -> str:
    """The `## Today` section of the Plan context."""
    cfg = get_config()["calendar"]
    d = on or today()
    lines = [f"{d.isoformat()} ({d.strftime('%A')}, {quarter_label(d)}, time zone {cfg['timezone']})",
             "Relative periods — use these bounds, do not work them out. The end bound",
             "is the day AFTER the period, so a timestamp late on its last day still counts:"]
    tz = ZoneInfo(cfg["timezone"])

    def at_midnight(day: date) -> str:
        # With the offset written out, the database reads the bound in the
        # configured zone rather than its own (UTC).
        return datetime(day.year, day.month, day.day, tzinfo=tz).isoformat(sep=" ")

    lines += [f"- {name}: >= '{at_midnight(a)}' AND < '{at_midnight(b + timedelta(days=1))}'"
              for name, (a, b) in ranges(d).items() if name != "today"]
    return "\n".join(lines)
