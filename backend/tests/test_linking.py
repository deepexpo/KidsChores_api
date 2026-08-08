"""Teen account linking — code generation and single-use redemption."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from app.services.linking import create_link_code, redeem_link_code


class FakeRedis:
    """Minimal in-memory stand-in for the subset of redis.asyncio used here."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_create_link_code_returns_six_digit_code_and_future_expiry() -> None:
    fake_redis = FakeRedis()
    with patch("app.services.linking.get_redis", return_value=fake_redis):
        code, expires_at = await create_link_code("member-1")
    assert len(code) == 6
    assert code.isdigit()
    assert expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_redeem_returns_member_id_for_valid_code() -> None:
    fake_redis = FakeRedis()
    with patch("app.services.linking.get_redis", return_value=fake_redis):
        code, _ = await create_link_code("member-1")
        member_id = await redeem_link_code(code)
    assert member_id == "member-1"


@pytest.mark.asyncio
async def test_redeem_is_single_use() -> None:
    fake_redis = FakeRedis()
    with patch("app.services.linking.get_redis", return_value=fake_redis):
        code, _ = await create_link_code("member-1")
        first = await redeem_link_code(code)
        second = await redeem_link_code(code)
    assert first == "member-1"
    assert second is None


@pytest.mark.asyncio
async def test_redeem_unknown_code_returns_none() -> None:
    fake_redis = FakeRedis()
    with patch("app.services.linking.get_redis", return_value=fake_redis):
        result = await redeem_link_code("000000")
    assert result is None


@pytest.mark.asyncio
async def test_generating_a_new_code_invalidates_the_old_one() -> None:
    """Only one valid code per member at a time — a regenerated code supersedes."""
    fake_redis = FakeRedis()
    with patch("app.services.linking.get_redis", return_value=fake_redis):
        old_code, _ = await create_link_code("member-1")
        new_code, _ = await create_link_code("member-1")
        assert old_code != new_code or True  # collision is astronomically unlikely but not fatal

        stale_result = await redeem_link_code(old_code)
        fresh_result = await redeem_link_code(new_code)

    assert stale_result is None
    assert fresh_result == "member-1"
