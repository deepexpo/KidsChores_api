"""
Redis-backed fixed-window rate limiter — used on /v1/auth/login and /v1/auth/register
to blunt credential stuffing (per-IP and per-email throttling).
"""

from __future__ import annotations

from app.db.redis import get_redis


class RateLimitExceededError(Exception):
    """Raised when a key has exceeded its allotted attempts within the window."""


async def check_rate_limit(key: str, max_attempts: int, window_seconds: int) -> None:
    """
    Increment the attempt counter for `key`; raise RateLimitExceededError if it's now
    over `max_attempts` within the current `window_seconds` fixed window.
    """
    redis = get_redis()
    redis_key = f"ratelimit:{key}"
    count = await redis.incr(redis_key)
    if count == 1:
        # First hit in this window — start the window's TTL.
        await redis.expire(redis_key, window_seconds)
    if count > max_attempts:
        raise RateLimitExceededError(f"Rate limit exceeded for '{key}'.")
