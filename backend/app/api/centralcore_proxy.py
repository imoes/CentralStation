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

import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response as PlainResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete, func, update

from app.api.deps import CurrentUser, get_db
from app.models.workflow import ComputerSession, UserPreference

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

class _CreateSessionBody(BaseModel):
    # Optional custom label (e.g. host name from an incident handoff). When omitted
    # the backend generates a sequential "Session N" label.
    label: str | None = None
    # Alert external_id for handoff sessions — persisted so the "✓ GELÖST"
    # button survives page reloads and container restarts.
    external_id: str | None = None


@router.post("/sessions", status_code=201)
async def create_session(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: _CreateSessionBody = _CreateSessionBody(),
    _: None = _ConsoleEnabled,
):
    """Create a new Hermes session, persist metadata in PostgreSQL."""
    from app.services.settings import get_active_llm_config, get_searxng_config
    try:
        llm = await get_active_llm_config(db)
        searxng = await get_searxng_config(db)
        llm_payload = {
            "llm_base_url": llm.base_url or None,
            "llm_model": llm.model or None,
            "llm_api_key": llm.api_key or None,
            "llm_api_mode": llm.api_mode or "chat_completions",
            "searxng_url": searxng.base_url if searxng.is_configured else None,
            "llm_timeout_seconds": llm.timeout_seconds or None,
        }
        log.info("Injecting LLM config for new session: model=%s mode=%s searxng=%s timeout=%ss",
                 llm.model or "(not set)", llm.api_mode,
                 searxng.base_url if searxng.is_configured else "(none)",
                 llm.timeout_seconds or "default")
    except Exception as exc:
        log.warning("Could not load LLM config, using CentralCore defaults: %s", exc)
        llm_payload = {}

    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(f"{CENTRALCORE_URL}/sessions", json=llm_payload)
    _check(r)
    data = r.json()
    sid = data["session_id"]

    # Use the caller's custom label (handoff host name) when provided. Otherwise
    # generate a label from the PostgreSQL session count — centralcore's in-memory
    # counter resets to 1 after every restart, causing duplicate "Session 1" labels.
    label = (body.label or "").strip()
    if not label:
        count_result = await db.execute(
            select(func.count(ComputerSession.id)).where(ComputerSession.user_id == user.id)
        )
        next_num = (count_result.scalar() or 0) + 1
        label = f"Session {next_num}"

    db.add(ComputerSession(
        id=sid, user_id=user.id, label=label,
        external_id=(body.external_id or None),
    ))
    await db.commit()
    log.info("Computer session %s created for user %s (label=%s, external_id=%s)",
             sid[:8], user.id, label, body.external_id or "-")
    return {**data, "label": label, "external_id": body.external_id or None}


@router.get("/sessions")
async def list_sessions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Return sessions from PostgreSQL (survives centralcore restarts)."""
    rows = (await db.execute(
        select(ComputerSession)
        .where(ComputerSession.user_id == user.id)
        .order_by(ComputerSession.created_at.asc())
    )).scalars().all()
    return [
        {
            "session_id": r.id,
            "label": r.label,
            "msg_count": r.msg_count,
            "created_at": r.created_at.isoformat(),
            "external_id": r.external_id,
            "resolved": r.resolved,
        }
        for r in rows
    ]


class _UpdateSessionBody(BaseModel):
    # Re-point a reused handoff session at a new alert. Setting external_id
    # resets resolved so the "✓ GELÖST" button reappears for the new alert.
    external_id: str | None = None
    label: str | None = None


@router.patch("/sessions/{sid}")
async def update_session(
    sid: str,
    body: _UpdateSessionBody,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Partial update: rename (label) or re-bind to a new alert (external_id).
    Only fields present in the request body are written."""
    values: dict = {}
    if body.label is not None:
        lbl = body.label.strip()[:120]
        if lbl:
            values["label"] = lbl
    if body.external_id is not None:
        # Alert re-bind: reset resolved so "✓ GELÖST" reappears for the new alert.
        values["external_id"] = body.external_id or None
        values["resolved"] = False
    if values:
        await db.execute(
            update(ComputerSession)
            .where(ComputerSession.id == sid, ComputerSession.user_id == user.id)
            .values(**values)
        )
        await db.commit()
    return {"ok": True, **values}


@router.delete("/sessions/{sid}")
async def delete_session(
    sid: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    # Delete from centralcore (best-effort — may already be gone after restart)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.delete(f"{CENTRALCORE_URL}/sessions/{sid}")
    except Exception as exc:
        log.debug("centralcore delete %s: %s (ignored)", sid[:8], exc)

    # Delete from PostgreSQL (authoritative)
    await db.execute(
        delete(ComputerSession).where(
            ComputerSession.id == sid,
            ComputerSession.user_id == user.id,
        )
    )
    await db.commit()
    return {"ok": True}


@router.post("/sessions/{sid}/to-workbench", status_code=201)
async def session_to_workbench(
    sid: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Transfer a Computer session to the Werkbank (Kanban). Idempotent."""
    from app.models.workflow import WorkSession

    cs = (await db.execute(
        select(ComputerSession).where(ComputerSession.id == sid, ComputerSession.user_id == user.id)
    )).scalar_one_or_none()
    if not cs:
        raise HTTPException(404, "Session not found")

    existing = (await db.execute(
        select(WorkSession).where(
            WorkSession.computer_session_id == sid,
            WorkSession.user_id == user.id,
        )
    )).scalars().first()
    if existing:
        return {"id": str(existing.id), "already_linked": True}

    ws = WorkSession(
        user_id=user.id,
        title=cs.label,
        computer_session_id=sid,
        status="in_progress",
        work_notes=[],
    )
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return {"id": str(ws.id), "already_linked": False}


@router.get("/sessions/{sid}/history", dependencies=[_ConsoleEnabled])
async def get_history(sid: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{CENTRALCORE_URL}/sessions/{sid}/history")
    _check(r)
    return r.json()


# ── Message → SSE stream (pass-through) ───────────────────────────

@router.post("/sessions/{sid}/message")
async def send_message(
    sid: str,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    import json as _json
    body = await request.body()
    log.debug("proxy message → centralcore session %s", sid[:8])

    # Inject active LLM config into every message so centralcore can use it
    # when restoring a session after a container restart (env-var defaults are
    # not configured in the centralcore container).
    try:
        from app.services.settings import get_active_llm_config, get_searxng_config, get_setting
        llm = await get_active_llm_config(db)
        searxng = await get_searxng_config(db)
        # Admin toggle: show the model's reasoning in the session (default ON).
        show_reasoning = (await get_setting(db, "computer.show_reasoning") or "true") != "false"
        body_data = _json.loads(body)
        body_data.update({
            "llm_base_url": llm.base_url or None,
            "llm_model": llm.model or None,
            "llm_api_key": llm.api_key or None,
            "llm_api_mode": llm.api_mode or "chat_completions",
            "searxng_url": searxng.base_url if searxng.is_configured else None,
            "llm_timeout_seconds": llm.timeout_seconds or None,
            "show_reasoning": show_reasoning,
        })
        body = _json.dumps(body_data).encode()
    except Exception as exc:
        log.debug("LLM config inject for message failed (non-fatal): %s", exc)

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
                    msg = "Session nicht mehr vorhanden — bitte neue Session starten." if resp.status_code == 404 \
                        else f"CentralCore-Fehler {resp.status_code}"
                    import json as _json
                    yield f'data: {_json.dumps({"type": "error", "text": msg})}\n\n'.encode()
                    return
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                except httpx.RemoteProtocolError:
                    # Hermes closes the SSE stream without a proper chunked-transfer
                    # terminator when the response is complete — this is expected.
                    pass

    # Increment msg_count in PostgreSQL (fire-and-forget, don't block SSE).
    # Uses a fresh session — the request's `db` may already be closed when this runs.
    async def _bump_msg_count() -> None:
        from app.core.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as fresh_db:
                await fresh_db.execute(
                    update(ComputerSession)
                    .where(ComputerSession.id == sid, ComputerSession.user_id == user.id)
                    .values(msg_count=ComputerSession.msg_count + 1)
                )
                await fresh_db.commit()
        except Exception as exc:
            log.debug("msg_count bump for %s failed: %s", sid[:8], exc)

    import asyncio
    asyncio.ensure_future(_bump_msg_count())

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


# ── Google TTS proxy ───────────────────────────────────────────────

class _TTSBody(BaseModel):
    text: str


@router.post("/tts", dependencies=[_ConsoleEnabled])
async def text_to_speech(body: _TTSBody) -> PlainResponse:
    """Proxy German TTS via Google Translate (unofficial endpoint, no key required).
    Goes through the corporate HTTP proxy configured via HTTP_PROXY env var."""
    text = body.text.strip()[:300]
    if not text:
        raise HTTPException(400, "Kein Text")
    qs = urllib.parse.urlencode({"ie": "UTF-8", "q": text, "tl": "de", "client": "tw-ob"})
    url = f"https://translate.google.com/translate_tts?{qs}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            })
        if r.status_code != 200:
            raise HTTPException(502, f"Google TTS: HTTP {r.status_code}")
        return PlainResponse(content=r.content, media_type="audio/mpeg")
    except httpx.TimeoutException:
        raise HTTPException(504, "Google TTS: Timeout")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Google TTS: {exc}")


# ── Helpers ────────────────────────────────────────────────────────

def _check(r: httpx.Response) -> None:
    if r.status_code >= 400:
        log.warning("CentralCore returned %s: %s", r.status_code, r.text[:200])
        raise HTTPException(r.status_code, f"CentralCore-Fehler: {r.text[:200]}")
