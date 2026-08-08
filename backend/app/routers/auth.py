"""
Auth router.

Sign in with Apple is implemented but deferred client-side to a later phase; email
+ password (§ below) is the current auth method. Both issue the same TokenResponse
shape and share the same JWT claims/TTLs and household-isolation rules.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_member
from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    validate_apple_identity_token,
)
from app.auth.passwords import hash_password, verify_password
from app.db.database import get_db
from app.db.models import AuthProvider, Household, Member, MemberRole
from app.schemas.schemas import (
    AppleSignInRequest,
    ChangePasswordRequest,
    EmailLoginRequest,
    EmailRegisterRequest,
    RefreshTokenRequest,
    TokenResponse,
)
from app.services.rate_limit import RateLimitExceededError, check_rate_limit
from app.services.refresh_tokens import redeem_refresh_token

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# Rate limits — per-IP and per-email fixed windows (PRD companion: docs/auth-endpoints.md).
_LOGIN_IP_LIMIT = (10, 15 * 60)  # 10 attempts / 15 min / IP
_LOGIN_EMAIL_LIMIT = (5, 15 * 60)  # 5 attempts / 15 min / email
_REGISTER_IP_LIMIT = (5, 60 * 60)  # 5 attempts / hour / IP
_REGISTER_EMAIL_LIMIT = (3, 60 * 60)  # 3 attempts / hour / email
_CHANGE_PASSWORD_LIMIT = (5, 15 * 60)  # 5 attempts / 15 min / member — guards current_password


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(*, ip: str, email: str, prefix: str,
                               ip_limit: tuple[int, int], email_limit: tuple[int, int]) -> None:
    try:
        await check_rate_limit(f"{prefix}:ip:{ip}", *ip_limit)
        await check_rate_limit(f"{prefix}:email:{email}", *email_limit)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
        ) from exc


@router.post("/apple", response_model=TokenResponse)
async def sign_in_with_apple(
    body: AppleSignInRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Validates an Apple identity token.
    - If the Apple sub has never been seen → creates a new household + parent member.
    - If already exists → issues fresh tokens.
    """
    try:
        payload = await validate_apple_identity_token(body.identity_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Apple identity token: {exc}",
        ) from exc

    apple_sub = payload["sub"]

    # Look up existing member by auth_subject
    result = await db.execute(
        select(Member).where(
            Member.auth_provider == AuthProvider.apple,
            Member.auth_subject == apple_sub,
        )
    )
    member = result.scalar_one_or_none()

    if member is None:
        # First-time sign-in: create household + parent profile
        household = Household(name=f"{body.display_name}'s Family")
        db.add(household)
        await db.flush()

        member = Member(
            household_id=household.id,
            role=MemberRole.parent,
            display_name=body.display_name,
            auth_provider=AuthProvider.apple,
            auth_subject=apple_sub,
        )
        db.add(member)
        await db.flush()
    else:
        result2 = await db.execute(
            select(Household).where(Household.id == member.household_id)
        )
        household = result2.scalar_one()

    return TokenResponse(
        access_token=create_access_token(member.id, household.id),
        refresh_token=create_refresh_token(member.id),
        member_id=member.id,
        household_id=household.id,
        role=member.role,
    )


# ── Email / password (current auth method — Apple deferred, see module docstring) ──────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    body: EmailRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Parent-only self-registration. Creates a new household + parent member, mirroring
    how Apple sign-in doubles as signup. Teens are minted by a parent via
    POST /v1/household/members/teens, not by self-registering here — a teen
    self-registering would create a *new* household instead of attaching to the
    one their parent already set up (no linking flow exists yet).
    """
    email = body.email.lower()
    await _enforce_rate_limit(
        ip=_client_ip(request), email=email, prefix="register",
        ip_limit=_REGISTER_IP_LIMIT, email_limit=_REGISTER_EMAIL_LIMIT,
    )

    existing = await db.execute(
        select(Member).where(
            Member.auth_provider == AuthProvider.email,
            Member.auth_subject == email,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email is already in use.",
        )

    household = Household(name=f"{body.display_name}'s Family")
    db.add(household)
    await db.flush()

    member = Member(
        household_id=household.id,
        role=MemberRole.parent,
        display_name=body.display_name,
        auth_provider=AuthProvider.email,
        auth_subject=email,
        password_hash=hash_password(body.password),
    )
    db.add(member)
    try:
        await db.flush()
    except IntegrityError as exc:
        # DB-level backstop against the race where two concurrent registrations for
        # the same email both pass the SELECT check above
        # (see uq_member_auth_provider_subject_global).
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email is already in use.",
        ) from exc

    return TokenResponse(
        access_token=create_access_token(member.id, household.id),
        refresh_token=create_refresh_token(member.id),
        member_id=member.id,
        household_id=household.id,
        role=member.role,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    body: EmailLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = body.email.lower()
    await _enforce_rate_limit(
        ip=_client_ip(request), email=email, prefix="login",
        ip_limit=_LOGIN_IP_LIMIT, email_limit=_LOGIN_EMAIL_LIMIT,
    )

    result = await db.execute(
        select(Member).where(
            Member.auth_provider == AuthProvider.email,
            Member.auth_subject == email,
        )
    )
    member = result.scalar_one_or_none()

    # Same generic error whether the email doesn't exist or the password is wrong —
    # don't reveal which one it was.
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password.",
    )
    if member is None or member.password_hash is None:
        raise invalid
    if not verify_password(body.password, member.password_hash):
        raise invalid

    household_result = await db.execute(
        select(Household).where(Household.id == member.household_id)
    )
    household = household_result.scalar_one()

    return TokenResponse(
        access_token=create_access_token(member.id, household.id),
        refresh_token=create_refresh_token(member.id),
        member_id=member.id,
        household_id=household.id,
        role=member.role,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Redeems a refresh token for a fresh token pair, rotating (single-using) it."""
    try:
        payload = decode_refresh_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from exc

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp or not await redeem_refresh_token(jti, int(exp)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used or is invalid.",
        )

    member_id = payload["sub"]
    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Member not found.")

    return TokenResponse(
        access_token=create_access_token(member.id, member.household_id),
        refresh_token=create_refresh_token(member.id),
        member_id=member.id,
        household_id=member.household_id,
        role=member.role,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Changes the caller's own password. Requires current_password so a
    borrowed, already-unlocked session can't silently take over the account —
    rate-limited per member for the same reason (blunt brute-forcing
    current_password via a stolen but valid access token).
    """
    if member.auth_provider != AuthProvider.email or member.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This account does not use a password.",
        )

    try:
        await check_rate_limit(f"change-password:member:{member.id}", *_CHANGE_PASSWORD_LIMIT)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
        ) from exc

    if not verify_password(body.current_password, member.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )

    member.password_hash = hash_password(body.new_password)
    db.add(member)
