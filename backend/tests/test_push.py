"""
APNs push service — config-gating and best-effort-never-raises behavior.

Doesn't test actual APNs wire protocol (aioapns' job, not ours) — tests that
this module correctly falls back to logging when unconfigured, builds the
client from settings when configured, and never lets a send failure surface
as an exception to the caller.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import push


@pytest.fixture(autouse=True)
def _reset_client() -> None:
    push.reset_client()


@pytest.mark.asyncio
async def test_unconfigured_settings_logs_instead_of_sending() -> None:
    with patch.object(push, "settings") as fake_settings:
        fake_settings.apns_team_id = ""
        fake_settings.apns_key_id = ""
        fake_settings.apns_private_key = ""
        with patch("app.services.push.APNs") as fake_apns_cls:
            await push.send_push("device-token", "Title", "Body")
    fake_apns_cls.assert_not_called()


@pytest.mark.asyncio
async def test_configured_settings_builds_client_and_sends() -> None:
    fake_client = AsyncMock()
    fake_result = MagicMock()
    fake_result.is_successful = True
    fake_client.send_notification = AsyncMock(return_value=fake_result)

    with patch.object(push, "settings") as fake_settings:
        fake_settings.apns_team_id = "TEAM123"
        fake_settings.apns_key_id = "KEY123"
        fake_settings.apns_private_key = "-----BEGIN PRIVATE KEY-----\nfake\n-----END KEY-----"
        fake_settings.apns_bundle_id = "com.kidschores.app"
        fake_settings.apns_env = "sandbox"
        with patch("app.services.push.APNs", return_value=fake_client) as fake_apns_cls:
            await push.send_push("device-token", "Title", "Body")

    fake_apns_cls.assert_called_once()
    fake_client.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_failure_does_not_raise() -> None:
    fake_client = AsyncMock()
    fake_client.send_notification = AsyncMock(side_effect=RuntimeError("APNs down"))

    with patch.object(push, "settings") as fake_settings:
        fake_settings.apns_team_id = "TEAM123"
        fake_settings.apns_key_id = "KEY123"
        fake_settings.apns_private_key = "fake-key"
        fake_settings.apns_bundle_id = "com.kidschores.app"
        fake_settings.apns_env = "sandbox"
        with patch("app.services.push.APNs", return_value=fake_client):
            await push.send_push("device-token", "Title", "Body")  # must not raise


@pytest.mark.asyncio
async def test_rejected_notification_does_not_raise() -> None:
    fake_client = AsyncMock()
    fake_result = MagicMock()
    fake_result.is_successful = False
    fake_result.status = "410"
    fake_result.description = "Unregistered"
    fake_client.send_notification = AsyncMock(return_value=fake_result)

    with patch.object(push, "settings") as fake_settings:
        fake_settings.apns_team_id = "TEAM123"
        fake_settings.apns_key_id = "KEY123"
        fake_settings.apns_private_key = "fake-key"
        fake_settings.apns_bundle_id = "com.kidschores.app"
        fake_settings.apns_env = "sandbox"
        with patch("app.services.push.APNs", return_value=fake_client):
            await push.send_push("device-token", "Title", "Body")  # must not raise
