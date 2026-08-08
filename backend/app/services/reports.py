"""
Report aggregation — completion rate, excuse frequency, and points earned,
bucketed into household-local weeks (Monday-start, matching the series-window
computation in app/routers/series.py's _compute_window).

Excused and cancelled instances are deliberately excluded from the
completion-rate denominator — only `complete` and `missed` count. This
follows the PRD directly: §8.2 says an excused task "does not count as missed
in reports," and §8.6 frames the whole report view around letting a parent
"notice a trend without policing individual instances" — a rate that
penalises accepted excuses would work against that.

Points-earned only counts entries that represent points a member actually
earned (task_completed, series_bonus, excused_partial) — not claims,
reversals, or manual adjustments, which aren't "earned."
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TypedDict

import pytz

from app.db.models import LedgerEntry, TaskInstance, TaskStatus


class ReportWeekDict(TypedDict):
    week_start: str
    completed: int
    missed: int
    completion_rate: float | None
    excuse_count: int
    points_earned: int


def week_start_of(dt: datetime, tz: pytz.BaseTzInfo) -> date:
    """Monday (household-local) of the week containing dt."""
    local_dt = dt.astimezone(tz)
    return (local_dt - timedelta(days=local_dt.weekday())).date()


def build_weekly_report(
    *,
    instances: list[TaskInstance],
    ledger_entries: list[LedgerEntry],
    range_start: date,
    num_weeks: int,
    tz: pytz.BaseTzInfo,
) -> list[ReportWeekDict]:
    """
    `instances` and `ledger_entries` should already be scoped to one member and
    to the [range_start, range_start + num_weeks) window by the caller (see
    app/routers/reports.py) — this function only buckets what it's given.

    Excuse frequency is bucketed by the week the *excuse was submitted*
    (excuse_submitted_at), not the task's due date — those can differ (an
    overdue task excused days later) — so it can occasionally fall into a
    week outside `instances`' own due-date range and be dropped; scoping
    excuses to "tasks due in this report's range" keeps the report's mental
    model simple (everything is about tasks due in the last N weeks).
    """
    buckets: dict[date, dict[str, int]] = {
        range_start + timedelta(weeks=i): {
            "completed": 0,
            "missed": 0,
            "excuse_count": 0,
            "points_earned": 0,
        }
        for i in range(num_weeks)
    }

    for inst in instances:
        bucket = buckets.get(week_start_of(inst.due_at, tz))
        if bucket is not None:
            if inst.status == TaskStatus.complete:
                bucket["completed"] += 1
            elif inst.status == TaskStatus.missed:
                bucket["missed"] += 1

        if inst.excuse_submitted_at is not None:
            excuse_bucket = buckets.get(week_start_of(inst.excuse_submitted_at, tz))
            if excuse_bucket is not None:
                excuse_bucket["excuse_count"] += 1

    for entry in ledger_entries:
        bucket = buckets.get(week_start_of(entry.created_at, tz))
        if bucket is not None:
            bucket["points_earned"] += entry.delta

    results: list[ReportWeekDict] = []
    for ws in sorted(buckets):
        b = buckets[ws]
        total = b["completed"] + b["missed"]
        rate = (b["completed"] / total) if total > 0 else None
        results.append(
            ReportWeekDict(
                week_start=ws.isoformat(),
                completed=b["completed"],
                missed=b["missed"],
                completion_rate=rate,
                excuse_count=b["excuse_count"],
                points_earned=b["points_earned"],
            )
        )
    return results
