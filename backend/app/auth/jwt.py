"""JWT authentication utilities."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger("auth.apple")
if not logger.handlers:
    # Same rationale as app/services/push.py: the root logger defaults to
    # WARNING, so this INFO-level diagnostic would otherwise be silently
    # dropped in an app that hasn't configured logging itself.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s auth.apple %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def create_access_token(member_id: str, household_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_expire_minutes)
    token: str = jwt.encode(
        {"sub": member_id, "hid": household_id, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token


def create_refresh_token(member_id: str, jti: str | None = None) -> str:
    """
    jti (JWT ID) identifies this specific refresh token so it can be tracked as
    single-use in Redis (see app.services.refresh_tokens) — this is what makes
    "rotate and invalidate the old one" actually enforceable for a stateless JWT.
    """
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days)
    token: str = jwt.encode(
        {"sub": member_id, "type": "refresh", "jti": jti or uuid.uuid4().hex, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Like decode_token, but requires the 'refresh' type claim — rejects access tokens."""
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")
    return payload


async def validate_apple_identity_token(identity_token: str) -> dict[str, Any]:
    """
    Validate an Apple identity token.
    Returns the decoded payload (includes 'sub' = Apple user ID).
    In production, fetch Apple's public keys from https://appleid.apple.com/auth/keys
    and verify the signature. For v0.1 we decode without verification in dev mode.
    """
    try:
        # Fetch Apple's public keys
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://appleid.apple.com/auth/keys")
            resp.raise_for_status()
            jwks = resp.json()

        # Decode header to get kid
        header = jwt.get_unverified_header(identity_token)
        kid = header.get("kid")

        # Find matching key
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if key is None:
            raise ValueError("Apple public key not found for kid.")

        from jose.backends import RSAKey
        public_key = RSAKey(key, algorithm="RS256")

        payload: dict[str, Any] = jwt.decode(
            identity_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.apple_bundle_id,
        )
        return payload
    except Exception as exc:
        # Diagnostic only — logs the token's *unverified* claims (never trusted
        # for auth) so a rejection's root cause (aud mismatch, expired token,
        # wrong kid, ...) is visible in server logs without needing the client
        # to hand over a live token out of band.
        try:
            unverified = jwt.get_unverified_claims(identity_token)
        except Exception:
            unverified = {}
        logger.warning(
            "apple identity token rejected: %s | expected_aud=%s got_aud=%s iss=%s exp=%s",
            exc,
            settings.apple_bundle_id,
            unverified.get("aud"),
            unverified.get("iss"),
            unverified.get("exp"),
        )
        raise
