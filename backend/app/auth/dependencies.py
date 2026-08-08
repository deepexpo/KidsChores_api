"""
FastAPI dependencies for auth and household isolation.

Household ID is NEVER accepted as a client parameter.
It is always derived from the authenticated JWT.
This is the primary tenant-isolation boundary.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token
from app.db.database import get_db
from app.db.models import Household, Member, MemberRole

bearer = HTTPBearer()


async def get_current_member(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Member:
    """Validate JWT and return the authenticated Member."""
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc

    member_id = payload.get("sub")
    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()

    if member is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Member not found.")
    return member


async def get_current_household(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
) -> Household:
    """Return the household derived from the authenticated member's JWT."""
    result = await db.execute(
        select(Household).where(Household.id == member.household_id)
    )
    household = result.scalar_one_or_none()
    if household is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household not found.")
    return household


async def require_parent(
    member: Member = Depends(get_current_member),
) -> Member:
    """Dependency that enforces the caller must be a parent."""
    if member.role != MemberRole.parent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires a parent account.",
        )
    return member


def require_teen_or_parent(member: Member = Depends(get_current_member)) -> Member:
    """Any authenticated member is allowed."""
    return member
