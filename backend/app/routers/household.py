"""Household and member management router."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_household, get_current_member, require_parent
from app.auth.pins import hash_pin, verify_pin
from app.db.database import get_db
from app.db.models import AuthProvider, Household, Member, MemberRole
from app.schemas.schemas import (
    HouseholdResponse,
    HouseholdUpdate,
    MemberResponse,
    TeenProfileCreate,
    VerifyPinRequest,
    VerifyPinResponse,
)
from app.services.rate_limit import RateLimitExceededError, check_rate_limit

router = APIRouter(prefix="/v1/household", tags=["household"])

# 5 attempts / 5 min, per member_id — a 4-digit PIN is only 10,000 combinations;
# this isn't a security boundary (master PRD §6.1) but shouldn't be brute-forceable
# in seconds. Deliberately not also rate-limited per-IP: this is a shared-device
# feature by design, so several teens' profiles get checked from the same iPad's
# IP in normal use — an IP-wide limit would lock out siblings for each other's
# attempts, which per-member_id doesn't.
_VERIFY_PIN_LIMIT = (5, 5 * 60)


# ── Household ─────────────────────────────────────────────────────────────────

@router.get("", response_model=HouseholdResponse)
async def get_household(
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
) -> Household:
    return household


@router.patch("", response_model=HouseholdResponse)
async def update_household(
    body: HouseholdUpdate,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> Household:
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(household, field, value)
    db.add(household)
    return household


# ── Members ───────────────────────────────────────────────────────────────────

@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    include_archived: bool = False,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[Member]:
    q = select(Member).where(Member.household_id == household.id)
    if not include_archived:
        q = q.where(Member.archived_at.is_(None))
    result = await db.execute(q)
    return list(result.scalars().all())


@router.post("/members/teens", response_model=MemberResponse, status_code=201)
async def create_teen_profile(
    body: TeenProfileCreate,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> Member:
    """
    Parent creates a teen profile.
    - Teens 13+ may have their own login (auth_subject set later via /auth/apple).
    - Shared-device mode: PIN protects the profile on a family iPad.
    - Birthdate must be >= 13 years ago (COPPA compliance).
    """
    birth_date = datetime.strptime(body.birthdate, "%Y-%m-%d").date()
    today = datetime.now(UTC).date()
    age_years = (today - birth_date).days // 365
    if age_years < 13:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Teen must be at least 13 years old (COPPA compliance).",
        )

    pin_hash = hash_pin(body.pin) if body.pin else None

    teen = Member(
        household_id=household.id,
        role=MemberRole.teen,
        display_name=body.display_name,
        birthdate=body.birthdate,
        auth_provider=AuthProvider.email,  # placeholder; updated when teen logs in
        auth_subject=f"pending-{household.id}-{body.display_name}",  # unique placeholder
        pin_hash=pin_hash,
    )
    db.add(teen)
    await db.flush()
    return teen


@router.post("/members/{member_id}/verify-pin", response_model=VerifyPinResponse)
async def verify_member_pin(
    member_id: str,
    body: VerifyPinRequest,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> VerifyPinResponse:
    """
    Shared-device PIN check (docs/auth-endpoints.md §5). Not a security boundary
    (master PRD §6.1) — friction against casual sibling access, not real auth.
    Always 200 on a well-formed request; a wrong PIN is a normal check result,
    not a request failure.
    """
    try:
        await check_rate_limit(f"verify-pin:member:{member_id}", *_VERIFY_PIN_LIMIT)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
        ) from exc

    result = await db.execute(
        select(Member).where(
            Member.id == member_id,
            Member.household_id == household.id,
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found.")

    if target.pin_hash is None:
        return VerifyPinResponse(valid=False, pin_set=False)
    return VerifyPinResponse(valid=verify_pin(body.pin, target.pin_hash), pin_set=True)


@router.delete("/members/{member_id}", status_code=204)
async def remove_member(
    member_id: str,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Archives (soft-deletes) a member — never a hard DELETE. A member with any
    activity (a completed task, a ledger entry, a reviewed excuse...) is
    referenced by created_by/reviewed_by/resolved_by columns that intentionally
    have no ON DELETE CASCADE (see Member.archived_at docstring), so a real
    DELETE would fail with a foreign-key violation for exactly the members most
    likely to be removed. Archiving preserves all history and matches the
    existing TaskDefinition/Series archive pattern.
    """
    result = await db.execute(
        select(Member).where(
            Member.id == member_id,
            Member.household_id == household.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    if member.role == MemberRole.parent and member.id == parent.id:
        raise HTTPException(
            status_code=422, detail="You cannot remove yourself as a parent."
        )
    member.archived_at = datetime.now(UTC)
    db.add(member)
