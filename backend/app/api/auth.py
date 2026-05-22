import hashlib
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.audit import AuditLog
from app.models.user import RefreshToken, User
from app.schemas.user import LoginRequest, PasswordChange, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "refresh_token"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        db.add(AuditLog(action="login_failed", resource_type="user",
                        resource_id=data.email))
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account inactive")

    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    db.add(RefreshToken(
        user_id=user.id,
        token_hash=_hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0
        )
    ))
    db.add(AuditLog(action="login_success", resource_type="user",
                    resource_id=str(user.id)))
    await db.commit()

    response.set_cookie(
        key=COOKIE_NAME, value=refresh_token,
        httponly=True, secure=True, samesite="strict", max_age=7 * 86400
    )
    return TokenResponse(access_token=access_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="No refresh token")

    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid refresh token")

    token_hash = _hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked.is_(False),
        )
    )
    stored = result.scalar_one_or_none()
    if not stored:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token revoked or not found")

    # Token Rotation
    stored.revoked = True
    user_result = await db.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one()

    new_access = create_access_token({"sub": str(user.id), "role": user.role})
    new_refresh = create_refresh_token({"sub": str(user.id)})
    db.add(RefreshToken(user_id=user.id, token_hash=_hash_token(new_refresh)))
    await db.commit()

    response.set_cookie(
        key=COOKIE_NAME, value=new_refresh,
        httponly=True, secure=True, samesite="strict", max_age=7 * 86400
    )
    return TokenResponse(access_token=new_access)


@router.post("/logout")
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    if refresh_token:
        token_hash = _hash_token(refresh_token)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        stored = result.scalar_one_or_none()
        if stored:
            stored.revoked = True
            await db.commit()

    response.delete_cookie(COOKIE_NAME)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser):
    return user


@router.post("/change-password")
async def change_password(
    data: PasswordChange,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password wrong")
    user.hashed_password = hash_password(data.new_password)
    db.add(AuditLog(action="password_changed", resource_type="user",
                    resource_id=str(user.id), user_id=user.id))
    await db.commit()
    return {"message": "Password changed"}
