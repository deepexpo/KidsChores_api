"""Password hashing tests — argon2id."""

from __future__ import annotations

from app.auth.passwords import hash_password, verify_password


def test_hash_is_not_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2")


def test_verify_correct_password() -> None:
    hashed = hash_password("hunter2pass")
    assert verify_password("hunter2pass", hashed) is True


def test_verify_wrong_password() -> None:
    hashed = hash_password("hunter2pass")
    assert verify_password("wrong-password", hashed) is False


def test_two_hashes_of_same_password_differ() -> None:
    """Argon2 salts each hash — two hashes of the same password must not match verbatim."""
    assert hash_password("hunter2pass") != hash_password("hunter2pass")
