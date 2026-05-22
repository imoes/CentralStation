from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.settings import GlobalSetting
from app.schemas.settings import SettingItem, SettingUpdate, SettingsResponse
from app.services.settings import get_all_settings, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])

SECRET_MASK = "••••••••"


@router.get("/", response_model=SettingsResponse, dependencies=[RequireAdmin])
async def get_settings(db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(GlobalSetting).order_by(GlobalSetting.key))
    rows = result.scalars().all()
    items: list[SettingItem] = []
    for row in rows:
        if row.is_secret:
            display = SECRET_MASK if row.value_encrypted else None
        else:
            display = row.value_plain
        items.append(SettingItem(key=row.key, value=display, is_secret=row.is_secret))
    return SettingsResponse(settings=items)


@router.patch("/{key}", response_model=SettingItem, dependencies=[RequireAdmin])
async def update_setting(
    key: str,
    data: SettingUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(404, f"Setting '{key}' not found")

    await set_setting(db, key, data.value)
    db.add(AuditLog(
        action="setting_updated",
        resource_type="setting",
        resource_id=key,
        user_id=current_user.id,
        new_value={"key": key, "value": "<secret>" if row.is_secret else data.value},
    ))
    await db.commit()

    # Return updated row (re-query after commit)
    result = await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    row = result.scalar_one()
    display = SECRET_MASK if (row.is_secret and row.value_encrypted) else row.value_plain
    return SettingItem(key=row.key, value=display, is_secret=row.is_secret)
