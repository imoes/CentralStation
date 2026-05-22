from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.settings import GlobalSetting
from app.schemas.settings import SettingItem, SettingUpdate, SettingsResponse
from app.services.settings import get_all_settings, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])


class TestResult(BaseModel):
    success: bool
    message: str
    detail: str | None = None

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


@router.post("/test/{group}", response_model=TestResult, dependencies=[RequireAdmin])
async def test_setting_group(
    group: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Test connectivity for a settings group using the currently stored values."""
    s = await get_all_settings(db)

    if group == "llm":
        url = (s.get("llm.base_url") or "").rstrip("/")
        if not url:
            return TestResult(success=False, message="LLM Basis-URL nicht konfiguriert")
        api_key = s.get("llm.api_key")
        model = s.get("llm.model") or ""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=8, verify=False) as client:
                r = await client.get(f"{url}/models", headers=headers)
            if r.status_code >= 400:
                return TestResult(
                    success=False,
                    message=f"HTTP {r.status_code}",
                    detail=r.text[:300],
                )
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            found = model in ids if model else None
            suffix = (
                f" — Modell '{model}' ✓" if found
                else f" — Modell '{model}' nicht in der Liste" if found is False
                else f" — {len(ids)} Modelle verfügbar"
            )
            return TestResult(success=True, message=f"Verbindung OK{suffix}")
        except httpx.ConnectError as e:
            return TestResult(success=False, message="Verbindung fehlgeschlagen", detail=str(e))
        except httpx.TimeoutException:
            return TestResult(success=False, message="Timeout (8 s) — Server nicht erreichbar")
        except Exception as e:
            return TestResult(success=False, message=str(e))

    elif group == "vision":
        url = (s.get("llm.vision_base_url") or "").rstrip("/")
        if not url:
            return TestResult(success=False, message="Vision-URL nicht konfiguriert")
        api_key = s.get("llm.vision_api_key")
        model = s.get("llm.vision_model") or ""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=8, verify=False) as client:
                r = await client.get(f"{url}/models", headers=headers)
            if r.status_code >= 400:
                return TestResult(
                    success=False,
                    message=f"HTTP {r.status_code}",
                    detail=r.text[:300],
                )
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            found = model in ids if model else None
            suffix = (
                f" — Modell '{model}' ✓" if found
                else f" — Modell '{model}' nicht in der Liste" if found is False
                else f" — {len(ids)} Modelle verfügbar"
            )
            return TestResult(success=True, message=f"Verbindung OK{suffix}")
        except httpx.ConnectError as e:
            return TestResult(success=False, message="Verbindung fehlgeschlagen", detail=str(e))
        except httpx.TimeoutException:
            return TestResult(success=False, message="Timeout (8 s) — Server nicht erreichbar")
        except Exception as e:
            return TestResult(success=False, message=str(e))

    elif group == "searxng":
        url = (s.get("searxng.base_url") or "").rstrip("/")
        if not url:
            return TestResult(success=False, message="SearXNG URL nicht konfiguriert")
        try:
            async with httpx.AsyncClient(timeout=8, verify=False) as client:
                r = await client.get(
                    f"{url}/search",
                    params={"q": "test", "format": "json"},
                )
            if r.status_code >= 400:
                return TestResult(
                    success=False,
                    message=f"HTTP {r.status_code}",
                    detail=r.text[:300],
                )
            n = len(r.json().get("results", []))
            return TestResult(success=True, message=f"Verbindung OK — {n} Ergebnisse für 'test'")
        except httpx.ConnectError as e:
            return TestResult(success=False, message="Verbindung fehlgeschlagen", detail=str(e))
        except httpx.TimeoutException:
            return TestResult(success=False, message="Timeout (8 s) — Server nicht erreichbar")
        except Exception as e:
            return TestResult(success=False, message=str(e))

    else:
        raise HTTPException(400, f"Unbekannte Gruppe '{group}'. Gültig: llm, vision, searxng")
