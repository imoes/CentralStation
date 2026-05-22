import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin, get_current_user
from app.core.database import get_db
from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

VALID_ROLES = {"admin", "sysadmin", "network_technician", "viewer"}


@router.get("/", response_model=list[UserResponse], dependencies=[RequireAdmin])
async def list_users(db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[RequireAdmin])
async def create_user(
    data: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    if data.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Valid: {VALID_ROLES}")

    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Email already exists")

    user = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    db.add(AuditLog(action="user_created", resource_type="user",
                    resource_id=data.email, user_id=current_user.id))
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserResponse, dependencies=[RequireAdmin])
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    if data.role and data.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Valid: {VALID_ROLES}")

    old = {"role": user.role, "is_active": user.is_active}
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active

    db.add(AuditLog(action="user_updated", resource_type="user",
                    resource_id=str(user_id), user_id=current_user.id,
                    old_value=old, new_value={"role": user.role, "is_active": user.is_active}))
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[RequireAdmin])
async def delete_user(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == current_user.id:
        raise HTTPException(400, "Cannot delete yourself")

    await db.delete(user)
    db.add(AuditLog(action="user_deleted", resource_type="user",
                    resource_id=str(user_id), user_id=current_user.id))
    await db.commit()
