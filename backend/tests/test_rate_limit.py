"""Rate limiter tests — fixed-window counting via Redis INCR/EXPIRE."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.rate_limit import RateLimitExceededError, check_rate_limit


@pytest.mark.asyncio
async def test_under_limit_does_not_raise() -> None:
    fake_redis = AsyncMock()
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock()
    with patch("app.services.rate_limit.get_redis", return_value=fake_redis):
        await check_rate_limit("login:ip:1.2.3.4", max_attempts=5, window_seconds=60)
    fake_redis.expire.assert_awaited_once_with("ratelimit:login:ip:1.2.3.4", 60)


@pytest.mark.asyncio
async def test_over_limit_raises() -> None:
    fake_redis = AsyncMock()
    fake_redis.incr = AsyncMock(return_value=6)
    fake_redis.expire = AsyncMock()
    with patch("app.services.rate_limit.get_redis", return_value=fake_redis):
        with pytest.raises(RateLimitExceededError):
            await check_rate_limit("login:ip:1.2.3.4", max_attempts=5, window_seconds=60)


@pytest.mark.asyncio
async def test_expire_only_set_on_first_hit() -> None:
    """EXPIRE should only be (re-)armed on the first increment of a window, not every call."""
    fake_redis = AsyncMock()
    fake_redis.incr = AsyncMock(return_value=3)
    fake_redis.expire = AsyncMock()
    with patch("app.services.rate_limit.get_redis", return_value=fake_redis):
        await check_rate_limit("login:ip:1.2.3.4", max_attempts=5, window_seconds=60)
    fake_redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_exactly_at_limit_does_not_raise() -> None:
    fake_redis = AsyncMock()
    fake_redis.incr = AsyncMock(return_value=5)
    fake_redis.expire = AsyncMock()
    with patch("app.services.rate_limit.get_redis", return_value=fake_redis):
        await check_rate_limit("login:ip:1.2.3.4", max_attempts=5, window_seconds=60)
