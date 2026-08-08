"""Shared-device PIN hashing tests (docs/auth-endpoints.md §5)."""

from __future__ import annotations

from app.auth.pins import hash_pin, verify_pin


def test_hash_is_not_plaintext() -> None:
    assert hash_pin("1234") != "1234"


def test_verify_correct_pin() -> None:
    assert verify_pin("1234", hash_pin("1234")) is True


def test_verify_wrong_pin() -> None:
    assert verify_pin("9999", hash_pin("1234")) is False


def test_hash_is_deterministic() -> None:
    """Unlike password hashing, PIN hashing is unsalted (matches the existing scheme
    set at teen-profile creation) — same input always produces the same hash."""
    assert hash_pin("1234") == hash_pin("1234")
