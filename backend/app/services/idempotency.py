"""
Idempotency-key enforcement for mutating endpoints (PRD §11.2).

Mobile clients retry on flaky networks. A double-tapped "complete" must not
produce two ledger entries — the DB partial-unique-index is the backstop,
but the API itself should return the original result on a replayed request
rather than surfacing a DuplicateLedgerEntryError.

Redis-backed: SET NX reserves the key; the first caller's JSON-serialisable
response is cached under it so replays short-circuit straight to a cached
response instead of re-running business logic.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from app.db.redis import get_redis

_TTL_SECONDS = 24 * 60 * 60  # 24h — long enough to cover any realistic retry window


async def run_idempotent[T](
    member_id: str,
    idempotency_key: str,
    handler: Callable[[], Awaitable[T]],
    serialize: Callable[[T], dict[str, object]],
    deserialize: Callable[[dict[str, object]], T],
) -> T:
    """
    Run `handler()` at most once per (member_id, idempotency_key).
    A replayed call with the same key returns the first call's cached result.
    """
    redis = get_redis()
    cache_key = f"idem:{member_id}:{idempotency_key}"

    cached = await redis.get(cache_key)
    if cached is not None:
        return deserialize(json.loads(cached))

    result = await handler()
    await redis.set(cache_key, json.dumps(serialize(result)), ex=_TTL_SECONDS, nx=True)
    return result
