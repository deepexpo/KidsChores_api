"""
APNs push delivery.

Config-gated: apns_team_id/apns_key_id/apns_private_key (app/config.py) are
all empty by default. When any is unset, send_push logs the notification
instead of sending it — the same visibility the old print()-based stub gave,
just structured — so local dev and CI never need real Apple credentials, and
nothing hangs or errors waiting on them. Once real credentials land in Fly
secrets, sending switches on with no code change.

Uses token-based auth (an APNs Auth Key, ES256-signed JWT) rather than a
per-app certificate — one key works across environments and doesn't expire
annually like certs do.
"""

from __future__ import annotations

import logging

from aioapns import APNs, NotificationRequest

from app.config import settings

logger = logging.getLogger("push")
if not logger.handlers:
    # Python's root logger defaults to WARNING, so an unconfigured app would
    # otherwise silently swallow the INFO-level "not sent, unconfigured" line
    # below — the one thing this fallback path needs to actually be visible
    # for (it's standing in for the old print()-based stub). Self-contained
    # so it doesn't depend on the rest of the app configuring logging.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s push %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_client: APNs | None = None
_client_built = False


def _get_client() -> APNs | None:
    global _client, _client_built
    if _client_built:
        return _client
    _client_built = True
    if not (settings.apns_team_id and settings.apns_key_id and settings.apns_private_key):
        return None
    _client = APNs(
        key=settings.apns_private_key,
        key_id=settings.apns_key_id,
        team_id=settings.apns_team_id,
        topic=settings.apns_bundle_id,
        use_sandbox=settings.apns_env == "sandbox",
    )
    return _client


def reset_client() -> None:
    """Test-only: forces _get_client() to rebuild from current settings."""
    global _client, _client_built
    _client = None
    _client_built = False


async def send_push(token: str, title: str, body: str) -> None:
    """
    Best-effort: never raises. A push failure (bad token, APNs outage,
    unconfigured credentials) should never fail the request/job that
    triggered it — the underlying state change already happened.
    """
    client = _get_client()
    if client is None:
        logger.info("push (unconfigured) -> %s... | %s: %s", token[:12], title, body)
        return

    request = NotificationRequest(
        device_token=token,
        message={"aps": {"alert": {"title": title, "body": body}, "sound": "default"}},
    )
    try:
        result = await client.send_notification(request)
    except Exception:
        logger.exception("push send failed -> %s...", token[:12])
        return
    if not result.is_successful:
        logger.warning(
            "push rejected -> %s... | status=%s description=%s",
            token[:12], result.status, result.description,
        )
