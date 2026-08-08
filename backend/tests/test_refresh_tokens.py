"""Refresh-token rotation tests — single-use enforcement via Redis SET NX."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.services.refresh_tokens import redeem_refresh_token


def _future_exp(seconds: int = 3600) -> int:
    return int((datetime.now(UTC) + timedelta(seconds=seconds)).timestamp())


@pytest.mark.asyncio
async def test_first_redemption_succeeds() -> None:
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)
    with patch("app.services.refresh_tokens.get_redis", return_value=fake_redis):
        result = await redeem_refresh_token("jti-1", _future_exp())
    assert result is True
    fake_redis.set.assert_awaited_once()
    _, kwargs = fake_redis.set.call_args
    assert kwargs["nx"] is True


@pytest.mark.asyncio
async def test_replayed_token_is_rejected() -> None:
    """SET NX returns None/False when the key already exists — a replayed jti."""
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=None)
    with patch("app.services.refresh_tokens.get_redis", return_value=fake_redis):
        result = await redeem_refresh_token("jti-1", _future_exp())
    assert result is False


@pytest.mark.asyncio
async def test_ttl_clamped_to_remaining_lifetime() -> None:
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)
    with patch("app.services.refresh_tokens.get_redis", return_value=fake_redis):
        await redeem_refresh_token("jti-1", _future_exp(seconds=120))
    _, kwargs = fake_redis.set.call_args
    assert 0 < kwargs["ex"] <= 120


@pytest.mark.asyncio
async def test_already_expired_token_uses_minimum_ttl() -> None:
    """An exp in the past must not produce a negative/zero Redis TTL."""
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)
    with patch("app.services.refresh_tokens.get_redis", return_value=fake_redis):
        await redeem_refresh_token("jti-1", _future_exp(seconds=-3600))
    _, kwargs = fake_redis.set.call_args
    assert kwargs["ex"] >= 1
