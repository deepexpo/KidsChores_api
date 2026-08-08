"""
Teen account linking.

A parent-created teen profile starts with a placeholder auth_subject
(f"pending-{household_id}-{display_name}") and no password — it only works in
shared-device PIN mode (household.py's create_teen_profile / verify_member_pin).
There was previously no way for a teen to attach their own login to that same
profile: registering via POST /v1/auth/register always creates a brand-new
household, which is wrong for a teen who already has one.

This closes that gap with a short-lived, single-use numeric code: a parent
generates one for a specific teen (create_link_code), tells the teen the code
out of band (verbally, text message, etc.), and the teen redeems it
(redeem_link_code) alongside their own email + password to convert their
existing member row in place — same id, same household, same role, just with
real credentials attached instead of the placeholder.

Codes live in Redis, not Postgres: they're transient by nature (24h TTL,
single-use) and this mirrors the existing refresh-token/idempotency-key
patterns rather than needing a migration for throwaway state.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from app.db.redis import get_redis

_CODE_TTL_SECONDS = 24 * 60 * 60  # 24 hours — long enough for "parent tells teen later today"


def _code_key(code: str) -> str:
    return f"link_code:{code}"


def _member_key(member_id: str) -> str:
    return f"link_code_for_member:{member_id}"


async def create_link_code(member_id: str) -> tuple[str, datetime]:
    """
    Generates a fresh 6-digit code for member_id, invalidating any code
    previously generated for that member (only one valid code per member at a
    time). Returns (code, expires_at).
    """
    redis = get_redis()

    previous_code = await redis.get(_member_key(member_id))
    if previous_code:
        await redis.delete(_code_key(previous_code))

    code = f"{secrets.randbelow(1_000_000):06d}"
    await redis.set(_code_key(code), member_id, ex=_CODE_TTL_SECONDS)
    await redis.set(_member_key(member_id), code, ex=_CODE_TTL_SECONDS)

    expires_at = datetime.now(UTC) + timedelta(seconds=_CODE_TTL_SECONDS)
    return code, expires_at


async def redeem_link_code(code: str) -> str | None:
    """
    Looks up and consumes (single-use) the member_id for `code`. Returns None
    if the code doesn't exist or has expired.
    """
    redis = get_redis()
    member_id = await redis.get(_code_key(code))
    if member_id is None:
        return None

    await redis.delete(_code_key(code))
    await redis.delete(_member_key(member_id))
    return str(member_id)
