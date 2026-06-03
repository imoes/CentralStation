import logging
from typing import Annotated

import httpx

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin, RequireSysAdmin
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.settings import GlobalSetting
from app.schemas.settings import SettingItem, SettingUpdate, SettingsResponse
from app.services.llm_client import generate_text
from app.services.settings import LLMConfig
from app.services.settings import get_all_settings, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])


class TestResult(BaseModel):
    success: bool
    message: str
    detail: str | None = None


class LLMStatusResponse(BaseModel):
    configured: bool
    base_url_set: bool
    model_set: bool


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


@router.get("/llm-status", response_model=LLMStatusResponse)
async def get_llm_status(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    s = await get_all_settings(db)
    base_url_set = bool(s.get("llm.base_url"))
    model_set = bool(s.get("llm.model"))
    return LLMStatusResponse(
        configured=base_url_set and model_set,
        base_url_set=base_url_set,
        model_set=model_set,
    )


@router.patch("/{key}", response_model=SettingItem, dependencies=[RequireAdmin])
async def update_setting(
    key: str,
    data: SettingUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    row = result.scalar_one_or_none()

    await set_setting(db, key, data.value)
    db.add(AuditLog(
        action="setting_updated",
        resource_type="setting",
        resource_id=key,
        user_id=current_user.id,
        new_value={"key": key, "value": "<secret>" if (row and row.is_secret) else data.value},
    ))
    await db.commit()

    # Reschedule jobs if an agent interval changed
    if key in ("agent.interval_minutes", "agent.aggregation_interval_minutes"):
        try:
            from app.services.ai_agent.scheduler import reschedule_jobs
            await reschedule_jobs()
        except Exception as exc:
            logger.warning("reschedule_jobs failed (non-fatal): %s", exc)

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
        model = s.get("llm.model") or ""
        if not model:
            return TestResult(success=False, message="LLM Modell nicht konfiguriert")
        llm_config = LLMConfig(
            base_url=url,
            model=model,
            api_key=s.get("llm.api_key"),
            timeout_seconds=int(s.get("llm.timeout_seconds") or 120),
            api_mode=s.get("llm.api_mode") or "chat_completions",
        )
        try:
            text = await generate_text(
                llm_config,
                [{"role": "user", "content": "Antworte nur mit OK."}],
                max_output_tokens=20,
                reasoning_effort="none",
            )
            return TestResult(
                success=True,
                message=f"Verbindung OK — Modell '{model}' antwortet",
                detail=text[:120] or None,
            )
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


@router.get("/codex-status", dependencies=[RequireAdmin])
async def get_codex_status(db: Annotated[AsyncSession, Depends(get_db)]):
    """Return Hermes OAuth status for the configured Codex provider."""
    from app.services.settings import get_all_settings
    from app.services.hermes_auth import get_hermes_provider_status, list_hermes_providers

    s = await get_all_settings(db)
    hermes_provider = s.get("llm.codex_hermes_provider") or "openai-codex"
    status = get_hermes_provider_status(hermes_provider)
    available = list_hermes_providers()
    return {
        **status,
        "available_providers": available,
        "fallback_enabled": s.get("llm.codex_fallback_enabled", "false") == "true",
    }


@router.post("/test/codex", response_model=TestResult, dependencies=[RequireAdmin])
async def test_codex_fallback(db: Annotated[AsyncSession, Depends(get_db)]):
    """Test the OpenAI Codex fallback configuration."""
    from app.services.settings import get_codex_config
    from app.services.llm_client import generate_text, LLMInvocationError

    codex_cfg = await get_codex_config(db)
    if not codex_cfg:
        from app.services.hermes_auth import get_hermes_provider_token
        from app.services.settings import get_all_settings
        s = await get_all_settings(db)
        provider = s.get("llm.codex_hermes_provider") or "openai-codex"
        token = get_hermes_provider_token(provider)
        if not token:
            return TestResult(
                success=False,
                message="Kein OAuth-Token gefunden",
                detail=f"Führe 'hermes auth {provider}' aus um dich einzuloggen.",
            )
        if s.get("llm.codex_fallback_enabled", "false") != "true":
            return TestResult(
                success=False,
                message="Fallback nicht aktiviert",
                detail="Aktiviere den Codex-Fallback in den Einstellungen.",
            )
        return TestResult(success=False, message="Konfiguration unvollständig")

    try:
        result = await generate_text(
            codex_cfg,
            [{"role": "user", "content": "Antworte nur mit: OK"}],
            max_output_tokens=10,
        )
        return TestResult(success=True, message=f"Verbindung OK — {codex_cfg.model} ({codex_cfg.base_url[:40]}...)")
    except LLMInvocationError as e:
        return TestResult(success=False, message="Verbindung fehlgeschlagen", detail=str(e)[:300])
    except Exception as e:
        return TestResult(success=False, message=str(e)[:200])
