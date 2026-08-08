"""
Domain-level push notification content and recipient selection (PRD §6.7).

send_push itself is mocked throughout — these tests check *who* gets
notified and *what* the message says, not delivery mechanics (that's
test_push.py's job).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import notifications


def make_member(*, push_token: str | None = "token-1", role: str = "parent") -> MagicMock:
    m = MagicMock()
    m.push_token = push_token
    m.role = role
    return m


def make_instance(*, assignee_push_token: str | None = "teen-token") -> MagicMock:
    inst = MagicMock()
    inst.definition.title = "Wash the car"
    inst.assignee = make_member(push_token=assignee_push_token, role="teen")
    return inst


@pytest.mark.asyncio
async def test_notify_new_excuse_sends_to_each_parent_with_a_token() -> None:
    parents = [make_member(push_token="p1"), make_member(push_token="p2")]
    db = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = parents
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    db.execute = AsyncMock(return_value=execute_result)

    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_new_excuse(db, "household-1", "Arjun", "Wash the car")

    assert fake_send.await_count == 2
    first_call = fake_send.await_args_list[0]
    assert first_call.args[0] == "p1"
    assert "Arjun" in first_call.kwargs["body"]
    assert "Wash the car" in first_call.kwargs["body"]


@pytest.mark.asyncio
async def test_notify_excuse_resolved_approved_copy() -> None:
    instance = make_instance()
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_excuse_resolved(instance, approved=True)
    fake_send.assert_awaited_once()
    args, kwargs = fake_send.call_args
    assert args[0] == "teen-token"
    assert kwargs["title"] == "Excuse approved"
    assert "approved" in kwargs["body"]


@pytest.mark.asyncio
async def test_notify_excuse_resolved_denied_copy() -> None:
    instance = make_instance()
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_excuse_resolved(instance, approved=False)
    kwargs = fake_send.call_args.kwargs
    assert kwargs["title"] == "Excuse denied"
    assert "denied" in kwargs["body"]


@pytest.mark.asyncio
async def test_notify_excuse_resolved_skips_assignee_without_token() -> None:
    instance = make_instance(assignee_push_token=None)
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_excuse_resolved(instance, approved=True)
    fake_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_task_reminder_sends_with_task_title() -> None:
    instance = make_instance()
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_task_reminder(instance)
    args, kwargs = fake_send.call_args
    assert args[0] == "teen-token"
    assert "Wash the car" in kwargs["body"]


@pytest.mark.asyncio
async def test_notify_task_reminder_skips_assignee_without_token() -> None:
    instance = make_instance(assignee_push_token=None)
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_task_reminder(instance)
    fake_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_series_expiring_sends_via_series_assignee() -> None:
    series_instance = MagicMock()
    series_instance.series.name = "Weekend Reset"
    series_instance.series.assignee = make_member(push_token="teen-token", role="teen")

    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_series_expiring(series_instance)
    args, kwargs = fake_send.call_args
    assert args[0] == "teen-token"
    assert "Weekend Reset" in kwargs["body"]


@pytest.mark.asyncio
async def test_notify_pending_approvals_skips_parent_without_token() -> None:
    parent = make_member(push_token=None)
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_pending_approvals(parent, 3)
    fake_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_pending_approvals_pluralizes_count() -> None:
    parent = make_member(push_token="p1")
    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send:
        await notifications.notify_pending_approvals(parent, 1)
    assert "1 item waiting" in fake_send.call_args.kwargs["body"]

    with patch("app.services.notifications.send_push", new=AsyncMock()) as fake_send2:
        await notifications.notify_pending_approvals(parent, 3)
    assert "3 items waiting" in fake_send2.call_args.kwargs["body"]
