"""
Refresh-token rotation.

JWTs are stateless, so "invalidate the old refresh token" on rotation requires an
external record of which tokens have already been redeemed. Each refresh token
carries a unique `jti`; redeeming one marks that jti as used in Redis, TTL'd to the
token's own remaining lifetime (from its `exp` claim) so the record never outlives
the token it guards. A second redemption of the same jti — a replayed/stolen
refresh token — is rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.redis import get_redis


async def redeem_refresh_token(jti: str, exp: int) -> bool:
    """
    Atomically mark a refresh-token jti as used. Returns True the first time (the
    caller may proceed and issue new tokens), False if it was already redeemed.

    `exp` is the token's own Unix expiry timestamp (the JWT's `exp` claim) — the
    Redis record's TTL is clamped to the token's actual remaining lifetime.
    """
    redis = get_redis()
    remaining = int(exp - datetime.now(UTC).timestamp())
    ttl_seconds = max(remaining, 1)
    # SET NX: only the first redemption succeeds.
    first_use = await redis.set(f"refresh_used:{jti}", "1", ex=ttl_seconds, nx=True)
    return bool(first_use)
