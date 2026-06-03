"""OAuth provider flows for LLM providers.

Implements browser-initiated OAuth (no CLI needed) for:
  - OpenAI Codex  (Device Code + PKCE, copied from Hermes auth.py)

Usage: Admin navigates to Einstellungen → KI, clicks "Mit OpenAI einloggen",
follows the device-code flow in the browser, CentralStation stores the token
encrypted in the DB.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, RequireAdmin

router = APIRouter(prefix="/oauth", tags=["oauth"])
log = logging.getLogger(__name__)

# ── OpenAI Codex constants (copied from Hermes auth.py) ──────────────────────
CODEX_CLIENT_ID   = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ISSUER      = "https://auth.openai.com"
CODEX_TOKEN_URL   = "https://auth.openai.com/oauth/token"
CODEX_DEVICE_URL  = "https://auth.openai.com/codex/device"       # user visits this
CODEX_USERCODE_EP = "/api/accounts/deviceauth/usercode"
CODEX_POLL_EP     = "/api/accounts/deviceauth/token"
CODEX_BASE_URL    = "https://chatgpt.com/backend-api/codex"
CODEX_POLL_SECS   = 5
CODEX_TIMEOUT_MIN = 15
CODEX_REFRESH_SKEW = 120  # seconds before expiry to proactively refresh

# In-memory store for ongoing auth sessions (cleaned up on completion/timeout)
# Key: session_id, Value: {device_auth_id, user_code, started_at, status, tokens}
_auth_sessions: dict[str, dict] = {}


def _jwt_exp(token: str) -> int | None:
    """Extract the exp claim from a JWT without validating the signature."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(payload.get("exp", 0))
    except Exception:
        return None


def _token_needs_refresh(access_token: str) -> bool:
    exp = _jwt_exp(access_token)
    if not exp:
        return False
    return time.time() + CODEX_REFRESH_SKEW >= exp


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _save_codex_tokens(db: AsyncSession, access_token: str, refresh_token: str) -> None:
    from app.services.settings import set_setting
    await set_setting(db, "llm.codex_access_token", access_token)
    await set_setting(db, "llm.codex_refresh_token", refresh_token)
    await set_setting(db, "llm.codex_authenticated_at", datetime.now(timezone.utc).isoformat())
    await db.commit()


async def _load_codex_tokens(db: AsyncSession) -> tuple[str | None, str | None]:
    from app.services.settings import get_setting
    access  = await get_setting(db, "llm.codex_access_token")
    refresh = await get_setting(db, "llm.codex_refresh_token")
    return access, refresh


async def _refresh_codex_token(refresh_token: str) -> tuple[str, str]:
    """Exchange a refresh token for a new access+refresh pair (copied from Hermes)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            CODEX_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise HTTPException(401, f"Token refresh failed: HTTP {resp.status_code}")
    data = resp.json()
    new_access  = data.get("access_token", "")
    new_refresh = data.get("refresh_token") or refresh_token  # some flows don't rotate
    if not new_access:
        raise HTTPException(401, "Refresh did not return access_token")
    return new_access, new_refresh


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/openai-codex/start", dependencies=[RequireAdmin])
async def codex_start(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Step 1: Start the device-code flow.
    Returns user_code + verification_uri to show in the browser.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{CODEX_ISSUER}{CODEX_USERCODE_EP}",
            json={"client_id": CODEX_CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"OpenAI device code request failed: HTTP {resp.status_code}")

    data = resp.json()
    user_code      = data.get("user_code", "")
    device_auth_id = data.get("device_auth_id", "")
    interval       = max(3, int(data.get("interval", CODEX_POLL_SECS)))

    if not user_code or not device_auth_id:
        raise HTTPException(502, "Invalid response from OpenAI device auth endpoint")

    session_id = str(uuid.uuid4())
    _auth_sessions[session_id] = {
        "device_auth_id": device_auth_id,
        "user_code": user_code,
        "interval": interval,
        "started_at": time.monotonic(),
        "status": "pending",
    }

    return {
        "session_id": session_id,
        "user_code": user_code,
        "verification_uri": CODEX_DEVICE_URL,
        "expires_in_minutes": CODEX_TIMEOUT_MIN,
        "poll_interval_seconds": interval,
    }


@router.post("/openai-codex/poll/{session_id}", dependencies=[RequireAdmin])
async def codex_poll(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Step 2: Poll for completion. Frontend calls this every ~5 seconds.
    Returns status: pending | authorized | timeout | error
    """
    session = _auth_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Auth session not found or expired")

    if session["status"] != "pending":
        return {"status": session["status"]}

    if time.monotonic() - session["started_at"] > CODEX_TIMEOUT_MIN * 60:
        session["status"] = "timeout"
        del _auth_sessions[session_id]
        return {"status": "timeout"}

    # Step 2a: Poll OpenAI for authorization code
    async with httpx.AsyncClient(timeout=15.0) as client:
        poll_resp = await client.post(
            f"{CODEX_ISSUER}{CODEX_POLL_EP}",
            json={
                "device_auth_id": session["device_auth_id"],
                "user_code": session["user_code"],
            },
            headers={"Content-Type": "application/json"},
        )

    if poll_resp.status_code in (403, 404):
        return {"status": "pending"}  # User hasn't completed yet

    if poll_resp.status_code != 200:
        session["status"] = "error"
        del _auth_sessions[session_id]
        raise HTTPException(502, f"Polling error: HTTP {poll_resp.status_code}")

    code_data          = poll_resp.json()
    authorization_code = code_data.get("authorization_code", "")
    code_verifier      = code_data.get("code_verifier", "")

    if not authorization_code or not code_verifier:
        return {"status": "pending"}

    # Step 2b: Exchange authorization_code for tokens (PKCE)
    redirect_uri = f"{CODEX_ISSUER}/deviceauth/callback"
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            CODEX_TOKEN_URL,
            data={
                "grant_type":    "authorization_code",
                "code":          authorization_code,
                "redirect_uri":  redirect_uri,
                "client_id":     CODEX_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_resp.status_code != 200:
        session["status"] = "error"
        del _auth_sessions[session_id]
        raise HTTPException(502, f"Token exchange failed: HTTP {token_resp.status_code}")

    tokens        = token_resp.json()
    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    if not access_token:
        session["status"] = "error"
        del _auth_sessions[session_id]
        raise HTTPException(502, "No access_token in token exchange response")

    # Persist tokens encrypted in DB
    await _save_codex_tokens(db, access_token, refresh_token)

    session["status"] = "authorized"
    del _auth_sessions[session_id]
    return {"status": "authorized"}


@router.get("/openai-codex/status", dependencies=[RequireAdmin])
async def codex_status(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return current auth status and auto-refresh if token is near expiry."""
    from app.services.settings import get_setting
    access, refresh = await _load_codex_tokens(db)
    if not access:
        return {"authenticated": False, "message": "Nicht eingeloggt"}

    if _token_needs_refresh(access) and refresh:
        try:
            new_access, new_refresh = await _refresh_codex_token(refresh)
            await _save_codex_tokens(db, new_access, new_refresh)
            access = new_access
            msg = "Token automatisch erneuert"
        except Exception as e:
            log.warning("codex token refresh failed: %s", e)
            msg = "Token-Erneuerung fehlgeschlagen — bitte neu einloggen"
            return {"authenticated": False, "message": msg}
    else:
        msg = "Eingeloggt"

    exp = _jwt_exp(access)
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else None
    auth_at = await get_setting(db, "llm.codex_authenticated_at")
    return {
        "authenticated": True,
        "message": msg,
        "expires_at": expires_at,
        "authenticated_at": auth_at,
        "base_url": CODEX_BASE_URL,
    }


@router.delete("/openai-codex/logout", dependencies=[RequireAdmin])
async def codex_logout(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove stored Codex tokens."""
    from app.services.settings import set_setting
    await set_setting(db, "llm.codex_access_token", None)
    await set_setting(db, "llm.codex_refresh_token", None)
    await set_setting(db, "llm.codex_authenticated_at", None)
    await db.commit()
    return {"ok": True}


# ── Token accessor for llm_client ────────────────────────────────────────────

async def get_codex_access_token(db: AsyncSession) -> str | None:
    """Return a valid Codex access token, refreshing if needed. None if not authenticated."""
    access, refresh = await _load_codex_tokens(db)
    if not access:
        return None
    if _token_needs_refresh(access) and refresh:
        try:
            new_access, new_refresh = await _refresh_codex_token(refresh)
            await _save_codex_tokens(db, new_access, new_refresh)
            return new_access
        except Exception as e:
            log.warning("codex auto-refresh failed: %s", e)
            return None
    return access
