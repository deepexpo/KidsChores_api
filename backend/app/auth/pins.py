"""
Shared-device PIN hashing (docs/auth-endpoints.md §5).

Explicitly *not* a security boundary (master PRD §6.1) — friction against casual
sibling access on a family iPad, not a real auth mechanism. SHA-256 (matching the
scheme already used at teen-profile creation in app/routers/household.py) is fine
for that threat model; the real protection against brute-forcing the 10,000-value
4-digit space is the rate limit on the verify endpoint, not the hash algorithm.
"""

from __future__ import annotations

import hashlib
import hmac


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


def verify_pin(pin: str, pin_hash: str) -> bool:
    return hmac.compare_digest(hash_pin(pin), pin_hash)
