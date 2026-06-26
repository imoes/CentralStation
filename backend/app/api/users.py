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
from app.models.workflow import UserPreference
from app.schemas.user import UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

VALID_ROLES = {"admin", "sysadmin", "network_technician", "viewer"}


@router.get("/", response_model=list[UserResponse], dependencies=[RequireAdmin])
async def list_users(db: Annotated[AsyncSession, Depends(get_db)]):
    users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    prefs_rows = (await db.execute(select(UserPreference))).scalars().all()
    prefs_map = {str(p.user_id): p for p in prefs_rows}
    result = []
    for u in users:
        pref = prefs_map.get(str(u.id))
        row = UserResponse.model_validate(u)
        row.computer_console_enabled = pref.computer_console_enabled if pref else False
        result.append(row)
    return result


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

    if data.computer_console_enabled is not None:
        prefs_result = await db.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )
        prefs = prefs_result.scalar_one_or_none()
        if prefs is None:
            prefs = UserPreference(user_id=user_id)
            db.add(prefs)
        prefs.computer_console_enabled = data.computer_console_enabled

    await db.commit()
    await db.refresh(user)
    row = UserResponse.model_validate(user)
    prefs_result2 = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    prefs2 = prefs_result2.scalar_one_or_none()
    row.computer_console_enabled = prefs2.computer_console_enabled if prefs2 else False
    return row


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
