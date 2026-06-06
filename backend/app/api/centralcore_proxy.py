"""CentralCore proxy — forwards /api/computer/* to the CentralCore container.

Adds JWT authentication and checks the computer_console_enabled preference
before forwarding any request. SSE streaming is passed through transparently.
The active LLM config (from CentralStation settings) is injected at session
creation so Hermes always uses the same model as the rest of CentralStation.
"""
from __future__ import annotations

import logging
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.workflow import UserPreference

router = APIRouter(prefix="/computer", tags=["computer"])
log = logging.getLogger(__name__)

CENTRALCORE_URL = os.environ.get("CENTRALCORE_URL", "http://centralcore:8001")


async def _require_console(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Raise 403 if the user does not have computer_console_enabled."""
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()
    if not prefs or not prefs.computer_console_enabled:
        raise HTTPException(403, "Computer Console ist für diesen Benutzer nicht aktiviert.")


_ConsoleEnabled = Depends(_require_console)


# ── Session CRUD ───────────────────────────────────────────────────

@router.post("/sessions", status_code=201, dependencies=[_ConsoleEnabled])
async def create_session(db: Annotated[AsyncSession, Depends(get_db)]):
    """Create a new Hermes session, injecting the active CentralStation LLM config."""
    from app.services.settings import get_active_llm_config
    try:
        llm = await get_active_llm_config(db)
        llm_payload = {
            "llm_base_url": llm.base_url or None,
            "llm_model": llm.model or None,
            "llm_api_key": llm.api_key or None,
            "llm_api_mode": llm.api_mode or "chat_completions",
        }
        log.info("Injecting LLM config for new session: model=%s mode=%s",
                 llm.model or "(not set)", llm.api_mode)
    except Exception as exc:
        log.warning("Could not load LLM config, using CentralCore defaults: %s", exc)
        llm_payload = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{CENTRALCORE_URL}/sessions", json=llm_payload)
    _check(r)
    return r.json()


@router.get("/sessions", dependencies=[_ConsoleEnabled])
async def list_sessions():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{CENTRALCORE_URL}/sessions")
    _check(r)
    return r.json()


@router.delete("/sessions/{sid}", dependencies=[_ConsoleEnabled])
async def delete_session(sid: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(f"{CENTRALCORE_URL}/sessions/{sid}")
    _check(r)
    return r.json()


@router.get("/sessions/{sid}/history", dependencies=[_ConsoleEnabled])
async def get_history(sid: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{CENTRALCORE_URL}/sessions/{sid}/history")
    _check(r)
    return r.json()


# ── Message → SSE stream (pass-through) ───────────────────────────

@router.post("/sessions/{sid}/message", dependencies=[_ConsoleEnabled])
async def send_message(sid: str, request: Request):
    body = await request.body()

    log.debug("proxy message → centralcore session %s", sid[:8])

    async def stream_gen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{CENTRALCORE_URL}/sessions/{sid}/message",
                content=body,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status_code >= 400:
                    err = await resp.aread()
                    log.warning("centralcore %s for session %s: %s",
                                resp.status_code, sid[:8], err[:200])
                    resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        stream_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Whisper STT ────────────────────────────────────────────────────

@router.post("/transcribe", dependencies=[_ConsoleEnabled])
async def transcribe(request: Request):
    body = await request.body()
    content_type = request.headers.get("content-type", "application/octet-stream")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{CENTRALCORE_URL}/transcribe",
            content=body,
            headers={"Content-Type": content_type},
        )
    _check(r)
    return r.json()


# ── Helpers ────────────────────────────────────────────────────────

def _check(r: httpx.Response) -> None:
    if r.status_code >= 400:
        log.warning("CentralCore returned %s: %s", r.status_code, r.text[:200])
        raise HTTPException(r.status_code, f"CentralCore-Fehler: {r.text[:200]}")
