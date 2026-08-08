"""Shared async Redis client — used by idempotency, refresh-token rotation, and rate limiting."""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis
