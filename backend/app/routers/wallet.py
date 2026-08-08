"""Wallet router — balance, ledger, claims, savings goals, manual adjustments."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_household, get_current_member, require_parent
from app.db.database import get_db
from app.db.models import (
    Claim,
    ClaimStatus,
    Household,
    LedgerEntry,
    Member,
    MemberRole,
    SavingsGoal,
)
from app.schemas.schemas import (
    ClaimCreate,
    ClaimResolveRequest,
    ClaimResponse,
    LedgerEntryResponse,
    ManualAdjustRequest,
    SavingsGoalCreate,
    SavingsGoalResponse,
    WalletResponse,
)
from app.services.ledger import LedgerService

router = APIRouter(prefix="/v1/wallet", tags=["wallet"])


# ── Claims ────────────────────────────────────────────────────────────────────
# Registered ahead of GET /{member_id} below: both are GET, and FastAPI/Starlette
# match routes in registration order — /v1/wallet/claims must be checked before
# the dynamic /v1/wallet/{member_id} or "claims" would be swallowed as a member_id.

@router.get("/claims", response_model=list[ClaimResponse])
async def list_claims(
    status: ClaimStatus | None = Query(None),
    member_id: str | None = None,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[ClaimResponse]:
    """
    Parent: all claims across the household, optionally filtered by status
    and/or member_id. Teen: their own claims only — this is the parent
    inbox's source for pending reward claims (they don't appear in
    GET /v1/approvals, which is task-completions/excuses only).
    """
    target_id: str | None
    if member.role == MemberRole.teen:
        if member_id and member_id != member.id:
            raise HTTPException(status_code=403, detail="You can only view your own claims.")
        target_id = member.id
    else:
        target_id = member_id

    q = (
        select(Claim)
        .join(Member, Claim.member_id == Member.id)
        .where(Member.household_id == household.id)
        .options(selectinload(Claim.member))
        .order_by(Claim.requested_at.asc())
    )
    if target_id:
        q = q.where(Claim.member_id == target_id)
    if status:
        q = q.where(Claim.status == status)

    result = await db.execute(q)
    claims = list(result.scalars().all())
    return [_to_claim_response(c, member_name=c.member.display_name) for c in claims]


@router.get("/{member_id}", response_model=WalletResponse)
async def get_wallet(
    member_id: str,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> WalletResponse:
    _assert_access(member, member_id)
    ledger = LedgerService(db)
    balance = await ledger.current_balance(member_id)

    # Active savings goal (most recent non-achieved)
    result = await db.execute(
        select(SavingsGoal)
        .where(SavingsGoal.member_id == member_id, SavingsGoal.achieved_at.is_(None))
        .order_by(SavingsGoal.created_at.desc())
        .limit(1)
    )
    goal = result.scalar_one_or_none()

    return WalletResponse(
        member_id=member_id,
        balance=balance,
        points_label=household.points_label,
        active_savings_goal=SavingsGoalResponse.model_validate(goal) if goal else None,
    )


@router.get("/{member_id}/ledger", response_model=list[LedgerEntryResponse])
async def get_ledger(
    member_id: str,
    limit: int = 50,
    offset: int = 0,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
) -> list[LedgerEntry]:
    _assert_access(member, member_id)
    ledger = LedgerService(db)
    return await ledger.get_ledger(member_id, limit=limit, offset=offset)


@router.post("/{member_id}/adjust", response_model=LedgerEntryResponse)
async def manual_adjust(
    member_id: str,
    body: ManualAdjustRequest,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> LedgerEntry:
    await _assert_same_household(member_id, household, db)
    ledger = LedgerService(db)
    return await ledger.manual_adjustment(member_id, body.delta, body.reason, parent)


@router.post("/claims", response_model=ClaimResponse, status_code=201)
async def submit_claim(
    body: ClaimCreate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    """
    Teen submits a claim. Points are NOT debited here — only on fulfilment.
    This prevents an unfulfilled claim from trapping the teen's balance.
    """
    claim = Claim(
        member_id=member.id,
        points=body.points,
        requested_item=body.requested_item,
    )
    db.add(claim)
    await db.flush()
    return _to_claim_response(claim, member_name=member.display_name)


@router.post("/claims/{claim_id}/resolve", response_model=ClaimResponse)
async def resolve_claim(
    claim_id: str,
    body: ClaimResolveRequest,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    """
    Parent fulfils or declines a claim.
    Points are debited only on fulfilment.
    """
    result = await db.execute(
        select(Claim)
        .join(Member, Claim.member_id == Member.id)
        .where(Claim.id == claim_id, Member.household_id == household.id)
        .options(selectinload(Claim.member))
    )
    claim = result.scalar_one_or_none()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")

    now = datetime.now(UTC)
    if body.approve:
        ledger = LedgerService(db)
        await ledger.fulfil_claim(claim, parent)
        claim.status = ClaimStatus.fulfilled
    else:
        claim.status = ClaimStatus.declined

    claim.parent_note = body.parent_note
    claim.resolved_at = now
    claim.resolved_by = parent.id
    db.add(claim)
    return _to_claim_response(claim, member_name=claim.member.display_name)


# ── Savings Goals (P1) ────────────────────────────────────────────────────────

@router.post("/{member_id}/goals", response_model=SavingsGoalResponse, status_code=201)
async def create_savings_goal(
    member_id: str,
    body: SavingsGoalCreate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
) -> SavingsGoal:
    _assert_access(member, member_id)
    goal = SavingsGoal(member_id=member_id, **body.model_dump())
    db.add(goal)
    await db.flush()
    return goal


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_claim_response(claim: Claim, member_name: str) -> ClaimResponse:
    """
    Builds the response explicitly rather than relying on ClaimResponse's
    from_attributes on the raw ORM object — member_name isn't a column, and
    accessing claim.member lazily outside an eager-loaded context is exactly
    the MissingGreenlet trap that bit task-completion under async SQLAlchemy
    elsewhere in this codebase. Callers must supply an already-known/loaded
    display name (either the caller's own, or via selectinload(Claim.member)).
    """
    return ClaimResponse(
        id=claim.id,
        member_id=claim.member_id,
        member_name=member_name,
        points=claim.points,
        requested_item=claim.requested_item,
        status=claim.status,
        parent_note=claim.parent_note,
        requested_at=claim.requested_at,
        resolved_at=claim.resolved_at,
    )


def _assert_access(member: Member, target_member_id: str) -> None:
    """Teens can only access their own wallet; parents can access any."""
    if member.role == MemberRole.teen and member.id != target_member_id:
        raise HTTPException(status_code=403, detail="You can only view your own wallet.")


async def _assert_same_household(
    member_id: str, household: Household, db: AsyncSession
) -> None:
    result = await db.execute(
        select(Member).where(
            Member.id == member_id,
            Member.household_id == household.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Member not found in this household.")
