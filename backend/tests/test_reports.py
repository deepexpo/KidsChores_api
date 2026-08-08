"""
Report bucketing tests (app/services/reports.py).

Key invariants:
- completion_rate = completed / (completed + missed); excused/cancelled never
  enter the denominator (PRD §8.2: excused "does not count as missed in
  reports").
- A week with no complete/missed tasks has completion_rate = None, not 0 or a
  ZeroDivisionError.
- Excuse frequency buckets by excuse_submitted_at, not due_at.
- points_earned sums whatever ledger entries the caller passed in — the
  earned-vs-not filtering happens in the router, not here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytz

from app.db.models import TaskStatus
from app.services.reports import build_weekly_report, week_start_of

UTC_TZ = pytz.UTC


def make_instance(
    status: TaskStatus,
    due_at: datetime,
    excuse_submitted_at: datetime | None = None,
) -> MagicMock:
    inst = MagicMock()
    inst.status = status
    inst.due_at = due_at
    inst.excuse_submitted_at = excuse_submitted_at
    return inst


def make_ledger_entry(created_at: datetime, delta: int) -> MagicMock:
    entry = MagicMock()
    entry.created_at = created_at
    entry.delta = delta
    return entry


def test_week_start_of_is_monday() -> None:
    # 2026-08-05 is a Wednesday
    wednesday = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    assert week_start_of(wednesday, UTC_TZ) == date(2026, 8, 3)


def test_completion_rate_excludes_excused_and_cancelled() -> None:
    monday = date(2026, 8, 3)
    week_dt = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    instances = [
        make_instance(TaskStatus.complete, week_dt),
        make_instance(TaskStatus.missed, week_dt),
        make_instance(TaskStatus.excused, week_dt),
        make_instance(TaskStatus.cancelled, week_dt),
    ]
    result = build_weekly_report(
        instances=instances, ledger_entries=[], range_start=monday, num_weeks=1, tz=UTC_TZ
    )
    week = result[0]
    assert week["completed"] == 1
    assert week["missed"] == 1
    # Rate is 1/2, not 1/4 — excused/cancelled never enter the denominator.
    assert week["completion_rate"] == 0.5


def test_completion_rate_is_none_when_no_complete_or_missed() -> None:
    monday = date(2026, 8, 3)
    week_dt = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    instances = [make_instance(TaskStatus.excused, week_dt)]
    result = build_weekly_report(
        instances=instances, ledger_entries=[], range_start=monday, num_weeks=1, tz=UTC_TZ
    )
    assert result[0]["completion_rate"] is None
    assert result[0]["completed"] == 0
    assert result[0]["missed"] == 0


def test_excuse_bucketed_by_submission_week_not_due_week() -> None:
    """A task due one week, excused the following week, should show the
    excuse in the week it was *submitted*."""
    week1_monday = date(2026, 8, 3)
    due_week1 = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)
    excused_week2 = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    instances = [
        make_instance(TaskStatus.excused, due_week1, excuse_submitted_at=excused_week2)
    ]
    result = build_weekly_report(
        instances=instances, ledger_entries=[], range_start=week1_monday, num_weeks=2, tz=UTC_TZ
    )
    assert result[0]["excuse_count"] == 0  # week of Aug 3 (due date)
    assert result[1]["excuse_count"] == 1  # week of Aug 10 (excuse submitted)


def test_points_earned_sums_ledger_deltas_per_week() -> None:
    monday = date(2026, 8, 3)
    entries = [
        make_ledger_entry(datetime(2026, 8, 4, 10, 0, tzinfo=UTC), 40),
        make_ledger_entry(datetime(2026, 8, 5, 10, 0, tzinfo=UTC), 30),
        make_ledger_entry(datetime(2026, 8, 11, 10, 0, tzinfo=UTC), 100),  # next week
    ]
    result = build_weekly_report(
        instances=[], ledger_entries=entries, range_start=monday, num_weeks=2, tz=UTC_TZ
    )
    assert result[0]["points_earned"] == 70
    assert result[1]["points_earned"] == 100


def test_out_of_range_rows_are_dropped_not_errored() -> None:
    """A row whose week falls outside [range_start, range_start+num_weeks) must
    be silently ignored, not raise a KeyError."""
    monday = date(2026, 8, 3)
    far_past = datetime(2020, 1, 1, 10, 0, tzinfo=UTC)
    instances = [make_instance(TaskStatus.complete, far_past)]
    entries = [make_ledger_entry(far_past, 999)]
    result = build_weekly_report(
        instances=instances, ledger_entries=entries, range_start=monday, num_weeks=1, tz=UTC_TZ
    )
    assert result[0]["completed"] == 0
    assert result[0]["points_earned"] == 0


def test_weeks_are_returned_oldest_first_and_cover_full_range() -> None:
    monday = date(2026, 7, 20)
    result = build_weekly_report(
        instances=[], ledger_entries=[], range_start=monday, num_weeks=4, tz=UTC_TZ
    )
    assert [w["week_start"] for w in result] == [
        "2026-07-20",
        "2026-07-27",
        "2026-08-03",
        "2026-08-10",
    ]


def test_household_timezone_affects_week_boundary() -> None:
    """A due_at just after UTC midnight can still belong to the *previous*
    household-local day/week in a negative-offset timezone."""
    tz = pytz.timezone("America/Los_Angeles")
    # Monday 2026-08-03 00:30 UTC == Sunday 2026-08-02 17:30 in LA (UTC-7 in August)
    due_at = datetime(2026, 8, 3, 0, 30, tzinfo=UTC)
    assert week_start_of(due_at, tz) == date(2026, 7, 27)  # the prior week's Monday
    assert week_start_of(due_at, UTC_TZ) == date(2026, 8, 3)  # different in UTC
