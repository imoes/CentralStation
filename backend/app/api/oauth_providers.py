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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, RequireAdmin, CurrentUser

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
    from app.services.settings import set_secret_setting, set_setting
    await set_secret_setting(db, "llm.codex_access_token", access_token)
    await set_secret_setting(db, "llm.codex_refresh_token", refresh_token)
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
    from app.services.settings import set_secret_setting, set_setting
    await set_secret_setting(db, "llm.codex_access_token", None)
    await set_secret_setting(db, "llm.codex_refresh_token", None)
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


# ══════════════════════════════════════════════════════════════════════════════
# Claude OAuth (Authorization Code + PKCE — same flow as `claude setup-token`)
# ══════════════════════════════════════════════════════════════════════════════
import hashlib
import secrets

CLAUDE_CLIENT_ID    = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
CLAUDE_TOKEN_URL     = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_REDIRECT_URI  = "https://console.anthropic.com/oauth/code/callback"
CLAUDE_SCOPES        = "org:create_api_key user:profile user:inference"

# session_id → {verifier, state, started_at}
_claude_auth_sessions: dict[str, dict] = {}


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _save_claude_tokens(
    db: AsyncSession, access_token: str, refresh_token: str, expires_at: str | None
) -> None:
    from app.services.settings import set_secret_setting, set_setting
    await set_secret_setting(db, "llm.claude_access_token", access_token)
    await set_secret_setting(db, "llm.claude_refresh_token", refresh_token)
    await set_setting(db, "llm.claude_expires_at", expires_at or "")
    await set_setting(db, "llm.claude_authenticated_at", datetime.now(timezone.utc).isoformat())
    await db.commit()


async def _load_claude_tokens(db: AsyncSession) -> tuple[str | None, str | None]:
    from app.services.settings import get_setting
    access  = await get_setting(db, "llm.claude_access_token")
    refresh = await get_setting(db, "llm.claude_refresh_token")
    return access, refresh


async def _refresh_claude_token(refresh_token: str) -> tuple[str, str, int]:
    """Exchange a refresh token for a new access+refresh pair. Returns (access, refresh, expires_in)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            CLAUDE_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLAUDE_CLIENT_ID,
            },
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(401, f"Claude token refresh failed: HTTP {resp.status_code}")
    data = resp.json()
    new_access  = data.get("access_token", "")
    new_refresh = data.get("refresh_token") or refresh_token
    expires_in  = int(data.get("expires_in", 0))
    if not new_access:
        raise HTTPException(401, "Claude refresh did not return access_token")
    return new_access, new_refresh, expires_in


def _claude_expires_at_iso(expires_in: int) -> str | None:
    if not expires_in:
        return None
    return datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).isoformat()


@router.post("/claude-oauth/start", dependencies=[RequireAdmin])
async def claude_start():
    """Step 1: Start the PKCE authorization flow.
    Returns an authorize_url to open in the browser. After authorizing, the user
    receives a code to paste back via /claude-oauth/complete.
    """
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    session_id = str(uuid.uuid4())
    _claude_auth_sessions[session_id] = {
        "verifier": verifier,
        "state": state,
        "started_at": time.monotonic(),
    }
    # claude.ai displays the authorization code for manual copy when code=true.
    params = {
        "code": "true",
        "client_id": CLAUDE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CLAUDE_REDIRECT_URI,
        "scope": CLAUDE_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    from urllib.parse import urlencode
    authorize_url = f"{CLAUDE_AUTHORIZE_URL}?{urlencode(params)}"
    return {
        "session_id": session_id,
        "authorize_url": authorize_url,
        "expires_in_minutes": 15,
    }


class _ClaudeCompleteBody(BaseModel):
    session_id: str
    code: str


@router.post("/claude-oauth/complete", dependencies=[RequireAdmin])
async def claude_complete(
    body: _ClaudeCompleteBody,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Step 2: Exchange the pasted authorization code for tokens."""
    session = _claude_auth_sessions.get(body.session_id)
    if not session:
        raise HTTPException(404, "Auth-Session nicht gefunden oder abgelaufen")

    # claude.ai returns the code in the form "<code>#<state>"; accept either.
    raw = body.code.strip()
    code_part = raw
    state_part = session["state"]
    if "#" in raw:
        code_part, state_part = raw.split("#", 1)

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            CLAUDE_TOKEN_URL,
            json={
                "grant_type":    "authorization_code",
                "code":          code_part,
                "state":         state_part,
                "client_id":     CLAUDE_CLIENT_ID,
                "redirect_uri":  CLAUDE_REDIRECT_URI,
                "code_verifier": session["verifier"],
            },
            headers={"Content-Type": "application/json"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(502, f"Claude Token-Austausch fehlgeschlagen: HTTP {token_resp.status_code} {token_resp.text[:200]}")

    tokens        = token_resp.json()
    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in    = int(tokens.get("expires_in", 0))

    if not access_token:
        raise HTTPException(502, "Kein access_token in Claude Token-Antwort")

    await _save_claude_tokens(db, access_token, refresh_token, _claude_expires_at_iso(expires_in))
    _claude_auth_sessions.pop(body.session_id, None)
    return {"status": "authorized"}


@router.get("/claude-oauth/status", dependencies=[RequireAdmin])
async def claude_oauth_status(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return current Claude auth status, auto-refreshing if near expiry."""
    from app.services.settings import get_setting
    access, refresh = await _load_claude_tokens(db)
    if not access:
        return {"authenticated": False, "message": "Nicht eingeloggt"}

    expires_at = await get_setting(db, "llm.claude_expires_at")
    # Refresh if expired or near-expiry
    needs_refresh = False
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            if time.time() + CODEX_REFRESH_SKEW >= exp_dt.timestamp():
                needs_refresh = True
        except Exception:
            pass

    msg = "Eingeloggt"
    if needs_refresh and refresh:
        try:
            new_access, new_refresh, expires_in = await _refresh_claude_token(refresh)
            await _save_claude_tokens(db, new_access, new_refresh, _claude_expires_at_iso(expires_in))
            expires_at = _claude_expires_at_iso(expires_in)
            msg = "Token automatisch erneuert"
        except Exception as e:
            log.warning("claude token refresh failed: %s", e)
            return {"authenticated": False, "message": "Token-Erneuerung fehlgeschlagen — bitte neu einloggen"}

    auth_at = await get_setting(db, "llm.claude_authenticated_at")
    return {
        "authenticated": True,
        "message": msg,
        "expires_at": expires_at or None,
        "authenticated_at": auth_at,
    }


@router.delete("/claude-oauth/logout", dependencies=[RequireAdmin])
async def claude_logout(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove stored Claude tokens."""
    from app.services.settings import set_secret_setting, set_setting
    await set_secret_setting(db, "llm.claude_access_token", None)
    await set_secret_setting(db, "llm.claude_refresh_token", None)
    await set_setting(db, "llm.claude_expires_at", None)
    await set_setting(db, "llm.claude_authenticated_at", None)
    await db.commit()
    return {"ok": True}


async def get_claude_access_token(db: AsyncSession) -> str | None:
    """Return a valid Claude access token, refreshing if needed. None if not authenticated."""
    from app.services.settings import get_setting
    access, refresh = await _load_claude_tokens(db)
    if not access:
        return None
    expires_at = await get_setting(db, "llm.claude_expires_at")
    if expires_at and refresh:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            if time.time() + CODEX_REFRESH_SKEW >= exp_dt.timestamp():
                new_access, new_refresh, expires_in = await _refresh_claude_token(refresh)
                await _save_claude_tokens(db, new_access, new_refresh, _claude_expires_at_iso(expires_in))
                return new_access
        except HTTPException:
            return None
        except Exception as e:
            log.warning("claude auto-refresh failed: %s", e)
    return access


# ══════════════════════════════════════════════════════════════════════════════
# Per-user OAuth flows (no admin required) — tokens returned to frontend,
# stored by the caller in the connector's encrypted_credentials (api_key field).
# ══════════════════════════════════════════════════════════════════════════════

_user_codex_sessions: dict[str, dict] = {}
_user_claude_sessions: dict[str, dict] = {}


@router.post("/openai-codex/user/start")
async def user_codex_start(user: CurrentUser):
    """Start Device Code flow for a personal LLM connector (any authenticated user)."""
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
    _user_codex_sessions[session_id] = {
        "user_id": str(user.id),
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


@router.post("/openai-codex/user/poll/{session_id}")
async def user_codex_poll(session_id: str, user: CurrentUser):
    """Poll for Device Code completion. Returns tokens on success — caller stores them.

    Response: {status: 'pending'|'authorized'|'timeout'|'error', access_token?, refresh_token?}
    """
    session = _user_codex_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Auth session not found or expired")
    if session["user_id"] != str(user.id):
        raise HTTPException(403, "Session belongs to another user")

    if session["status"] != "pending":
        return {"status": session["status"]}

    if time.monotonic() - session["started_at"] > CODEX_TIMEOUT_MIN * 60:
        session["status"] = "timeout"
        _user_codex_sessions.pop(session_id, None)
        return {"status": "timeout"}

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
        return {"status": "pending"}

    if poll_resp.status_code != 200:
        session["status"] = "error"
        _user_codex_sessions.pop(session_id, None)
        raise HTTPException(502, f"Polling error: HTTP {poll_resp.status_code}")

    code_data          = poll_resp.json()
    authorization_code = code_data.get("authorization_code", "")
    code_verifier      = code_data.get("code_verifier", "")

    if not authorization_code or not code_verifier:
        return {"status": "pending"}

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
        _user_codex_sessions.pop(session_id, None)
        raise HTTPException(502, f"Token exchange failed: HTTP {token_resp.status_code}")

    tokens        = token_resp.json()
    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    if not access_token:
        session["status"] = "error"
        _user_codex_sessions.pop(session_id, None)
        raise HTTPException(502, "No access_token in token exchange response")

    _user_codex_sessions.pop(session_id, None)
    return {
        "status": "authorized",
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


@router.post("/claude-oauth/user/start")
async def user_claude_start(user: CurrentUser):
    """Start PKCE authorization flow for a personal LLM connector."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    session_id = str(uuid.uuid4())
    _user_claude_sessions[session_id] = {
        "user_id": str(user.id),
        "verifier": verifier,
        "state": state,
        "started_at": time.monotonic(),
    }
    params = {
        "code": "true",
        "client_id": CLAUDE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CLAUDE_REDIRECT_URI,
        "scope": CLAUDE_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    from urllib.parse import urlencode
    authorize_url = f"{CLAUDE_AUTHORIZE_URL}?{urlencode(params)}"
    return {
        "session_id": session_id,
        "authorize_url": authorize_url,
        "expires_in_minutes": 15,
    }


class _UserClaudeCompleteBody(BaseModel):
    session_id: str
    code: str


@router.post("/claude-oauth/user/complete")
async def user_claude_complete(body: _UserClaudeCompleteBody, user: CurrentUser):
    """Exchange the authorization code for tokens. Returns them — caller stores in connector."""
    session = _user_claude_sessions.get(body.session_id)
    if not session:
        raise HTTPException(404, "Auth-Session nicht gefunden oder abgelaufen")
    if session["user_id"] != str(user.id):
        raise HTTPException(403, "Session belongs to another user")

    raw = body.code.strip()
    code_part = raw
    state_part = session["state"]
    if "#" in raw:
        code_part, state_part = raw.split("#", 1)

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            CLAUDE_TOKEN_URL,
            json={
                "grant_type":    "authorization_code",
                "code":          code_part,
                "state":         state_part,
                "client_id":     CLAUDE_CLIENT_ID,
                "redirect_uri":  CLAUDE_REDIRECT_URI,
                "code_verifier": session["verifier"],
            },
            headers={"Content-Type": "application/json"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(502, f"Claude Token-Austausch fehlgeschlagen: HTTP {token_resp.status_code}")

    tokens        = token_resp.json()
    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in    = int(tokens.get("expires_in", 0))

    if not access_token:
        raise HTTPException(502, "Kein access_token in Claude Token-Antwort")

    _user_claude_sessions.pop(body.session_id, None)
    return {
        "status": "authorized",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": _claude_expires_at_iso(expires_in),
    }
