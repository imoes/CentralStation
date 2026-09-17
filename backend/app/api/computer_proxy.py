"""Computer Console proxy — forwards /api/computer/* to the Hermes service in the userenv container.

Adds JWT authentication and checks the computer_console_enabled preference
before forwarding any request. SSE streaming is passed through transparently.
The active LLM config (from CentralStation settings) is injected at session
creation so Hermes always uses the same model as the rest of CentralStation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
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
from app.models.workflow import ComputerSession, UserPreference, WorkSession
from app.models.connector import ConnectorConfig
from app.services.codex_models import extract_codex_model_ids

router = APIRouter(prefix="/computer", tags=["computer"])
log = logging.getLogger(__name__)

def _internal_client(**kwargs) -> httpx.AsyncClient:
    """httpx client for intra-Docker requests (bypasses HTTP_PROXY env var)."""
    return httpx.AsyncClient(trust_env=False, **kwargs)


def _target_url(user_id: str) -> str:
    """Return the per-user Hermes container URL (http://cs-userenv-{uid}:8001)."""
    from app.services.userenv_manager import hermes_url
    return hermes_url(str(user_id))


async def _reapply_ssh_if_recreated(db: AsyncSession, user_id) -> None:
    """Re-inject SSH config/key if ensure_container just recreated the container.

    The per-container ~/.ssh is ephemeral (no volume), so an on-demand recreation
    (crash, docker rm, prune) drops the marvin key and reverts to the entrypoint
    fallback config. configure_ssh otherwise only runs on explicit session-create,
    so SSH would silently break until then. This closes that gap for the message
    and history proxy paths, which recreate the container but never reconfigured it.
    """
    from app.services.userenv_manager import consume_just_created, configure_ssh
    if not await asyncio.to_thread(consume_just_created, str(user_id)):
        return
    creds = await _load_ssh_creds(db, user_id)
    if not creds:
        return
    try:
        await asyncio.to_thread(
            configure_ssh, str(user_id),
            creds.get("username", ""), creds.get("private_key", ""),
            creds.get("password", ""),
        )
        log.info("re-applied SSH creds after container recreation for %s", user_id)
    except Exception as exc:
        log.warning("_reapply_ssh_if_recreated failed for %s: %s", user_id, exc)


async def _load_ssh_creds(db: AsyncSession, user_id) -> dict | None:
    """Load the user's SSH connector credentials, or None if not configured."""
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials as _dec
    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == "ssh",
            ConnectorConfig.owner_user_id == user_id,
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if not conn:
        return None
    return _dec(conn.encrypted_credentials)


async def _load_agent_creds(db: AsyncSession, user_id, agent_type: str) -> dict | None:
    """Load stored CLI agent credentials (claude_cli or codex_cli connector).

    For claude_cli: if the stored access token is expired (or within 10 min of
    expiry), transparently refresh it via the stored refresh token and persist the
    new pair. Without this the token silently dies after ~8h and the Console fails
    with "Not logged in" until the user re-authenticates manually.
    """
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials as _dec
    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == agent_type,
            ConnectorConfig.owner_user_id == user_id,
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if not conn:
        return None
    creds = _dec(conn.encrypted_credentials)

    if agent_type == "claude_cli":
        creds = await _refresh_claude_cli_if_expired(db, conn, creds)
    return creds


async def _sync_claude_token_from_volume(db: AsyncSession, user_id) -> None:
    """Capture a CLI-rotated claude token from the container volume back into the DB.

    The native `claude` CLI refreshes and rotates the token in ~/.claude/.credentials.json.
    Persisting the newer token into the DB keeps the backup current, so a volume wipe can
    re-seed a still-valid token instead of forcing a re-auth. Only updates when the volume
    token is strictly newer than the DB copy.
    """
    import datetime as _dt
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials as _dec, encrypt_credentials as _enc
    from app.services.userenv_manager import read_claude_oauth, _expires_to_ms
    try:
        oauth = await asyncio.to_thread(read_claude_oauth, str(user_id))
        if not oauth or not oauth.get("refreshToken"):
            return
        res = await db.execute(
            _sel(ConnectorConfig).where(
                ConnectorConfig.type == "claude_cli",
                ConnectorConfig.owner_user_id == user_id,
            ).limit(1)
        )
        conn = res.scalar_one_or_none()
        if not conn:
            return
        creds = _dec(conn.encrypted_credentials)
        try:
            vol_ms = int(oauth.get("expiresAt") or 0)
        except (ValueError, TypeError):
            vol_ms = 0
        if vol_ms <= _expires_to_ms(creds.get("expires_at")):
            return  # DB already holds the newest token
        creds.update({
            "access_token": oauth.get("accessToken", ""),
            "refresh_token": oauth.get("refreshToken", ""),
            "expires_at": _dt.datetime.fromtimestamp(vol_ms / 1000, tz=_dt.timezone.utc).isoformat(),
        })
        conn.encrypted_credentials = _enc(creds)
        await db.commit()
        log.info("claude_cli token synced from volume (CLI rotation) for user %s", user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("_sync_claude_token_from_volume failed for %s: %s", user_id, exc)


async def _refresh_claude_cli_if_expired(db: AsyncSession, conn, creds: dict) -> dict:
    """Refresh an expired claude_cli access token via its refresh token (in place)."""
    import datetime as _dt
    from app.core.security import encrypt_credentials as _enc

    exp_raw = creds.get("expires_at") or ""
    refresh = creds.get("refresh_token") or ""
    if not refresh:
        return creds
    # Parse ISO expiry; treat unparseable/empty as "expired".
    expired = True
    try:
        exp_dt = _dt.datetime.fromisoformat(exp_raw)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=_dt.timezone.utc)
        now = _dt.datetime.now(_dt.timezone.utc)
        expired = exp_dt <= now + _dt.timedelta(minutes=10)
    except (ValueError, TypeError):
        expired = True
    if not expired:
        return creds

    try:
        from app.api.oauth_providers import _refresh_claude_token, _claude_expires_at_iso
        new_access, new_refresh, expires_in = await _refresh_claude_token(refresh)
        creds = {
            **creds,
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expires_at": _claude_expires_at_iso(expires_in) or "",
        }
        conn.encrypted_credentials = _enc(creds)
        await db.commit()
        log.info("claude_cli token refreshed for connector %s", conn.id)
    except Exception as exc:  # noqa: BLE001
        log.warning("claude_cli token refresh failed (user must re-auth): %s", exc)
    return creds


async def _upsert_agent_connector(
    db: AsyncSession, user_id, agent_type: str, creds: dict
) -> None:
    """Upsert a ConnectorConfig row for the given CLI agent type."""
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import encrypt_credentials as _enc, decrypt_credentials as _dec
    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == agent_type,
            ConnectorConfig.owner_user_id == user_id,
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if conn and "model" not in creds:
        existing = _dec(conn.encrypted_credentials)
        if existing.get("model"):
            creds["model"] = existing["model"]
    enc = _enc(creds)
    if conn:
        conn.encrypted_credentials = enc
    else:
        conn = ConnectorConfig(
            name=f"Computer Console {agent_type}",
            type=agent_type,
            owner_user_id=user_id,
            enabled=True,
            encrypted_credentials=enc,
        )
        db.add(conn)
    await db.commit()


async def _get_console_llm_config(db: AsyncSession, user_id) -> "LLMConfig | None":
    """Return the user's personal Console LLM config (type='console_llm'), or None.

    None means: fall back to get_active_llm_config() (global admin LLM).
    """
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials as _dec
    from app.services.settings import _llm_config_from_connector
    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == "console_llm",
            ConnectorConfig.owner_user_id == user_id,
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if not conn:
        return None
    creds = _dec(conn.encrypted_credentials)
    return _llm_config_from_connector(conn, creds)


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


# ── Console Agent Configuration ────────────────────────────────────

class _ConfigureAgentBody(BaseModel):
    agent: str                         # "hermes" | "claude_cli" | "codex_cli"
    access_token: str | None = None    # Claude PKCE / Codex Device-Code OAuth token
    refresh_token: str | None = None
    expires_at: str | None = None      # ISO string (Claude only)


@router.post("/configure-agent", status_code=200)
async def configure_agent(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: _ConfigureAgentBody,
    _: None = _ConsoleEnabled,
):
    """Set the Computer Console agent + inject credentials into the userenv container."""
    from app.services.userenv_manager import (
        ensure_container, configure_claude_credentials, configure_codex_credentials,
    )

    if body.agent not in ("hermes", "claude_cli", "codex_cli"):
        raise HTTPException(400, "agent muss 'hermes', 'claude_cli' oder 'codex_cli' sein")

    if body.agent == "claude_cli":
        # Fresh token from an OAuth flow → store it; otherwise fall back to the
        # credentials already saved for this user (explicit "activate" button —
        # switching the active agent must not require re-authenticating).
        if body.access_token:
            await _upsert_agent_connector(db, user.id, "claude_cli", {
                "access_token": body.access_token,
                "refresh_token": body.refresh_token or "",
                "expires_at": body.expires_at or "",
            })
            creds = {"access_token": body.access_token,
                     "refresh_token": body.refresh_token or "",
                     "expires_at": body.expires_at or ""}
        else:
            creds = await _load_agent_creds(db, user.id, "claude_cli")
            if not creds or not creds.get("access_token"):
                raise HTTPException(400, "Kein Claude-Token gespeichert — bitte zuerst authentifizieren")
        await asyncio.to_thread(ensure_container, str(user.id))
        # Capture any CLI-rotated token before (maybe) seeding, so we never clobber a
        # newer volume token. A fresh OAuth (body.access_token) force-overwrites to
        # allow switching accounts; the "activate" path (no token) respects the volume.
        await _sync_claude_token_from_volume(db, user.id)
        await asyncio.to_thread(
            configure_claude_credentials,
            str(user.id), creds["access_token"], creds.get("refresh_token") or "",
            creds.get("expires_at") or None, None, bool(body.access_token),
        )

    elif body.agent == "codex_cli":
        if body.access_token:
            await _upsert_agent_connector(db, user.id, "codex_cli", {
                "access_token": body.access_token,
                "refresh_token": body.refresh_token or "",
            })
            creds = {"access_token": body.access_token}
        else:
            creds = await _load_agent_creds(db, user.id, "codex_cli")
            if not creds or not creds.get("access_token"):
                raise HTTPException(400, "Kein Codex-Token gespeichert — bitte zuerst authentifizieren")
        await asyncio.to_thread(ensure_container, str(user.id))
        await asyncio.to_thread(configure_codex_credentials, str(user.id), creds["access_token"])

    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    pref = result.scalar_one_or_none()
    if pref:
        pref.computer_agent = body.agent
        await db.commit()

    log.info("Computer Console agent set to '%s' for user %s", body.agent, user.id)
    return {"agent": body.agent, "status": "configured"}


@router.get("/agent-credentials/{agent_type}", status_code=200)
async def get_agent_credentials(
    agent_type: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
):
    """Internal endpoint: userenv containers call this to re-inject expired CLI credentials.

    Authentication: The request must carry an X-CS-User-ID header identifying the container's
    user. This endpoint is only reachable within the Docker-internal cs-net network — no JWT
    is used since the container has no user session.
    """
    import uuid as _uuid
    user_id = request.headers.get("X-CS-User-ID", "").strip()
    if not user_id:
        raise HTTPException(400, "X-CS-User-ID header erforderlich")
    try:
        _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "X-CS-User-ID muss eine gültige UUID sein")
    if agent_type not in ("claude_cli", "codex_cli"):
        raise HTTPException(400, "agent_type muss 'claude_cli' oder 'codex_cli' sein")

    creds = await _load_agent_creds(db, user_id, agent_type)
    if not creds:
        raise HTTPException(404, f"Keine Credentials für {agent_type} / user {user_id}")

    return {
        "access_token": creds.get("access_token", ""),
        "refresh_token": creds.get("refresh_token", ""),
        "expires_at": creds.get("expires_at", ""),
    }


# ── CLI Model Selection ────────────────────────────────────────────

_CLAUDE_FALLBACK = [
    "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5",
    "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]
_CODEX_FALLBACK = [
    "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark",
    "codex-auto-review",
]
async def _fetch_codex_models(access_token: str) -> list[str]:
    """Fetch Codex models with the ChatGPT OAuth token.

    The per-user Device Code token is accepted by the ChatGPT Codex backend,
    not by the public OpenAI API model-list endpoint.
    """
    from app.api.oauth_providers import CODEX_BASE_URL

    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(
            f"{CODEX_BASE_URL.rstrip('/')}/models?client_version=1.0.0",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code != 200:
        log.debug("Codex model fetch failed: HTTP %s %s", r.status_code, r.text[:200])
        return []
    return extract_codex_model_ids(r.json())


@router.get("/models/{provider}")
async def list_cli_models(
    provider: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Return available models for claude or codex CLI, fetched live from the provider API.

    Falls back to a curated static list when the OAuth token is missing or the call fails.
    Returns the currently stored model preference as current_model.
    """
    if provider not in ("claude", "codex"):
        raise HTTPException(400, "provider muss 'claude' oder 'codex' sein")

    agent_type = "claude_cli" if provider == "claude" else "codex_cli"
    creds = await _load_agent_creds(db, user.id, agent_type)
    current_model = (creds or {}).get("model", "") or ""
    fallback = _CLAUDE_FALLBACK if provider == "claude" else _CODEX_FALLBACK

    if not creds or not creds.get("access_token"):
        return {
            "models": fallback,
            "source": "static",
            "current_model": current_model,
            "authenticated": False,
        }

    access_token = creds["access_token"]
    try:
        if provider == "claude":
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "anthropic-version": "2023-06-01",
                    },
                )
            if r.status_code == 200:
                models = [m["id"] for m in r.json().get("data", [])]
                if models:
                    return {
                        "models": sorted(models),
                        "source": "api",
                        "current_model": current_model,
                        "authenticated": True,
                    }
        else:
            models = await _fetch_codex_models(access_token)
            if models:
                return {
                    "models": models,
                    "source": "api",
                    "current_model": current_model,
                    "authenticated": True,
                }
    except Exception as exc:
        log.debug("Model fetch for %s failed: %s", provider, exc)

    return {
        "models": fallback,
        "source": "static",
        "current_model": current_model,
        "authenticated": True,
    }


class _CliModelBody(BaseModel):
    provider: str   # "claude" | "codex"
    model: str


@router.patch("/cli-model", status_code=200)
async def set_cli_model(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: _CliModelBody,
    _: None = _ConsoleEnabled,
):
    """Store model preference inside the CLI agent's ConnectorConfig credentials."""
    if body.provider not in ("claude", "codex"):
        raise HTTPException(400, "provider muss 'claude' oder 'codex' sein")
    model = body.model.strip()
    if not model:
        raise HTTPException(400, "model darf nicht leer sein")

    agent_type = f"{body.provider}_cli"
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import encrypt_credentials as _enc, decrypt_credentials as _dec

    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == agent_type,
            ConnectorConfig.owner_user_id == user.id,
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, f"Kein {agent_type}-Connector für diesen Benutzer")

    creds = _dec(conn.encrypted_credentials)
    creds["model"] = model
    conn.encrypted_credentials = _enc(creds)
    await db.commit()

    log.info("CLI model set to '%s' for %s / user %s", model, agent_type, user.id)
    return {"status": "saved", "model": model}


# ── Hermes Console LLM Config ──────────────────────────────────────

class _ConsoleLLMBody(BaseModel):
    api_mode: str = "chat_completions"  # chat_completions | anthropic_messages | codex_responses | bedrock_converse
    model: str = ""
    base_url: str = ""
    api_key: str | None = None          # None = keep existing
    timeout_seconds: int = 120
    thinking_mode: bool = False
    use_global: bool = False            # True = delete personal config, fall back to global


@router.get("/hermes-llm")
async def get_hermes_llm(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Return the user's personal Hermes Console LLM config (or None → uses global)."""
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials as _dec
    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == "console_llm",
            ConnectorConfig.owner_user_id == user.id,
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if not conn:
        return {"configured": False}
    creds = _dec(conn.encrypted_credentials)
    api_mode = creds.get("api_mode") or "chat_completions"
    model = creds.get("model") or ""
    if not model and api_mode == "codex_responses":
        model = "gpt-5.5"
    elif not model and api_mode == "anthropic_messages":
        model = "claude-opus-4-8"
    return {
        "configured": True,
        "api_mode": api_mode,
        "model": model,
        "base_url": conn.base_url or "",
        "timeout_seconds": int(creds.get("timeout_seconds") or 120),
        "thinking_mode": str(creds.get("thinking_mode", "false")).lower() == "true",
        "has_api_key": bool(creds.get("api_key")),
    }


@router.put("/hermes-llm", status_code=200)
async def put_hermes_llm(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: _ConsoleLLMBody,
    _: None = _ConsoleEnabled,
):
    """Save (or delete) the user's personal Hermes Console LLM config."""
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import encrypt_credentials as _enc, decrypt_credentials as _dec

    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == "console_llm",
            ConnectorConfig.owner_user_id == user.id,
        ).limit(1)
    )
    conn = res.scalar_one_or_none()

    if body.use_global:
        if conn:
            await db.delete(conn)
            await db.commit()
        return {"status": "deleted", "message": "Nutzt jetzt globale LLM-Konfiguration"}

    # Preserve existing api_key when client sends None (masked field)
    existing_key = ""
    if conn and body.api_key is None:
        existing_key = _dec(conn.encrypted_credentials).get("api_key") or ""

    model = (body.model or "").strip()
    if not model and body.api_mode == "codex_responses":
        model = "gpt-5.5"
    elif not model and body.api_mode == "anthropic_messages":
        model = "claude-opus-4-8"

    creds = {
        "api_mode": body.api_mode,
        "model": model,
        "api_key": body.api_key if body.api_key is not None else existing_key,
        "timeout_seconds": body.timeout_seconds,
        "thinking_mode": "true" if body.thinking_mode else "false",
    }
    enc = _enc(creds)

    if conn:
        conn.base_url = body.base_url
        conn.encrypted_credentials = enc
        conn.enabled = True
    else:
        conn = ConnectorConfig(
            name="Computer Console Hermes LLM",
            type="console_llm",
            owner_user_id=user.id,
            enabled=True,
            base_url=body.base_url,
            encrypted_credentials=enc,
        )
        db.add(conn)
    await db.commit()
    log.info("Console LLM config saved for user %s (mode=%s model=%s)", user.id, body.api_mode, body.model)
    return {"status": "saved"}


# ── Session CRUD ───────────────────────────────────────────────────

class _CreateSessionBody(BaseModel):
    # Optional custom label (e.g. host name from an incident handoff). When omitted
    # the backend generates a sequential "Session N" label.
    label: str | None = None
    # Alert external_id for handoff sessions — persisted so the "✓ GELÖST"
    # button survives page reloads and container restarts.
    external_id: str | None = None
    ticket_connector_id: uuid.UUID | None = None
    ticket_issue_id: str | None = None
    ticket_key: str | None = None
    context_hash: str | None = None


@router.post("/sessions", status_code=201)
async def create_session(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: _CreateSessionBody = _CreateSessionBody(),
    _: None = _ConsoleEnabled,
):
    """Create a session, or return the persisted session for the same Jira issue."""
    from app.services.settings import get_active_llm_config, get_searxng_config
    from app.models.connector import ConnectorConfig
    if bool(body.ticket_connector_id) != bool(body.ticket_issue_id):
        raise HTTPException(status_code=422, detail="ticket_connector_id and ticket_issue_id must be provided together")
    if body.ticket_connector_id and body.ticket_issue_id:
        connector = (await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.id == body.ticket_connector_id,
                ConnectorConfig.type.in_(("jira", "jira_sd")),
                ConnectorConfig.enabled.is_(True),
                ((ConnectorConfig.owner_user_id == user.id) | ConnectorConfig.owner_user_id.is_(None)),
            )
        )).scalar_one_or_none()
        if not connector:
            raise HTTPException(status_code=404, detail="Jira connector not found")
        existing = (await db.execute(
            select(ComputerSession).where(
                ComputerSession.user_id == user.id,
                ComputerSession.ticket_connector_id == body.ticket_connector_id,
                ComputerSession.ticket_issue_id == body.ticket_issue_id,
            )
        )).scalar_one_or_none()
        if existing:
            return {
                "session_id": existing.id,
                "label": existing.label,
                "external_id": existing.external_id,
                "agent_type": existing.agent_type,
                "ticket_ref": {
                    "connector_id": str(existing.ticket_connector_id),
                    "issue_id": existing.ticket_issue_id,
                    "key": existing.ticket_key,
                },
                "context_hash": existing.context_hash,
                "has_activity_snapshot": bool(existing.ticket_activity_snapshot),
                "context_synced_at": existing.context_synced_at.isoformat() if existing.context_synced_at else None,
                "last_activity_at": existing.last_activity_at.isoformat(),
                "reused": True,
            }
    _ssh_creds: dict | None = None
    extra_servers: dict = {}  # personal MCP connectors; defined here so it survives
                              # an early exception in the LLM-config block below.
    try:
        # For Hermes sessions: prefer console-specific LLM config; fall back to
        # the global admin LLM config. Do not pass user_id here: user-scoped
        # personal LLM connectors would override the explicit "global" mode.
        # CLI sessions (claude_cli/codex_cli) use their own OAuth tokens — LLM config irrelevant.
        _agent_pref = (await db.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        )).scalar_one_or_none()
        _agent_type = getattr(_agent_pref, "computer_agent", None) or "hermes"
        if _agent_type == "hermes":
            llm = (await _get_console_llm_config(db, user.id)) or (await get_active_llm_config(db))
        else:
            llm = await get_active_llm_config(db, user_id=user.id)
        searxng = await get_searxng_config(db)
        llm_payload = {
            "llm_base_url": llm.base_url or None,
            "llm_model": llm.model or None,
            "llm_api_key": llm.api_key or None,
            "llm_api_mode": llm.api_mode or "chat_completions",
            "searxng_url": searxng.base_url if searxng.is_configured else None,
            "llm_timeout_seconds": llm.timeout_seconds or None,
        }
        log.info("Injecting LLM config for new session (agent=%s): model=%s mode=%s timeout=%ss",
                 _agent_type, llm.model or "(not set)", llm.api_mode,
                 llm.timeout_seconds or "default")

        # Build per-user MCP server config from personal connectors.
        # Written to {workspaces_base}/{user_id}/hermes_config.yaml and mounted
        # into the container as /root/.hermes/config.yaml (read by Hermes at startup).
        from sqlalchemy import select as _sel
        from app.core.security import decrypt_credentials as _dec
        import base64 as _b64

        mcp_res = await db.execute(
            _sel(ConnectorConfig).where(
                ConnectorConfig.type == "mcp_server",
                ConnectorConfig.owner_user_id == user.id,
                ConnectorConfig.enabled.is_(True),
            )
        )
        for conn in mcp_res.scalars().all():
            creds = _dec(conn.encrypted_credentials)
            srv: dict = {
                "transport": creds.get("transport", "streamable-http"),
                "url": conn.base_url.rstrip("/"),
            }
            if creds.get("token"):
                srv["headers"] = {"Authorization": creds["token"]}
            srv_name = conn.name.lower().replace(" ", "-") or "mcp-user"
            extra_servers[srv_name] = srv

        # VibeMK (CheckMK MCP) — system-managed, credentials from the CheckMK
        # connector. The tier follows the user's checkmk_admin permission: without it
        # the agent gets the URL of the read+operational instance, so CheckMK
        # configuration tools are unreachable rather than merely discouraged.
        try:
            from app.services.vibemk_manager import ensure_vibemk, TIER_ADMIN, TIER_DEFAULT
            _tier = TIER_ADMIN if getattr(user, "checkmk_admin", False) else TIER_DEFAULT
            _vurl, _vreason = await ensure_vibemk(db, _tier)
            if _vurl:
                if "vibemk" in extra_servers:
                    log.info("vibemk: personal MCP connector superseded by the "
                             "system-managed instance for user %s", user.id)
                extra_servers["vibemk"] = {"transport": "streamable-http", "url": _vurl}
            else:
                log.warning("vibemk not registered for %s: %s", user.id, _vreason)
        except Exception as exc:  # noqa: BLE001
            log.warning("vibemk registration failed for %s: %s", user.id, exc)

        awx_res = await db.execute(
            _sel(ConnectorConfig).where(
                ConnectorConfig.type == "awx_ng",
                ConnectorConfig.owner_user_id == user.id,
                ConnectorConfig.enabled.is_(True),
            ).limit(1)
        )
        awx_conn = awx_res.scalar_one_or_none()
        if awx_conn:
            creds = _dec(awx_conn.encrypted_credentials)
            username = creds.get("username", "")
            password = creds.get("password", "")
            b64 = _b64.b64encode(f"{username}:{password}".encode()).decode()
            extra_servers["awx-ng"] = {
                "transport": "streamable-http",
                "url": awx_conn.base_url.rstrip("/") + "/mcp/",
                "headers": {"Authorization": f"Basic {b64}"},
            }

        # Load SSH settings and inject username into session (for system prompt override)
        _ssh_creds = await _load_ssh_creds(db, user.id)
        if _ssh_creds:
            llm_payload["ssh_username"] = _ssh_creds.get("username", "")
    except Exception as exc:
        log.warning("Could not load LLM config, using Hermes defaults: %s", exc)
        llm_payload = {}

    # Determine which Console agent this user has configured.
    _pref_res = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    _pref = _pref_res.scalar_one_or_none()
    agent_type = getattr(_pref, "computer_agent", None) or "hermes"
    llm_payload["agent_type"] = agent_type

    # Write per-user hermes_config.yaml (centralstation + personal connectors).
    # Done BEFORE ensure_container so the file is ready when the container starts
    # and mounts it as /root/.hermes/config.yaml.
    from app.services.userenv_manager import (
        ensure_container, configure_ssh, write_hermes_config,
        configure_claude_credentials, configure_codex_credentials,
    )
    try:
        await asyncio.to_thread(write_hermes_config, str(user.id), extra_servers)
    except Exception as exc:
        log.warning("write_hermes_config failed for %s: %s", user.id, exc)

    try:
        await asyncio.to_thread(ensure_container, str(user.id))
        if _ssh_creds:
            await asyncio.to_thread(
                configure_ssh, str(user.id),
                _ssh_creds.get("username", ""), _ssh_creds.get("private_key", ""),
                _ssh_creds.get("password", ""),
            )
        # Re-inject CLI agent credentials at session create (codex: not on volume).
        if agent_type == "claude_cli":
            # Capture a CLI-rotated token into the DB before loading/seeding so we keep
            # the native refresh chain intact (no clobbering with a stale DB copy).
            await _sync_claude_token_from_volume(db, user.id)
            _claude_creds = await _load_agent_creds(db, user.id, "claude_cli")
            if _claude_creds:
                # Pass extra_servers so configure_claude_credentials registers all personal
                # MCP connectors (VibeMK, AWX-NG, etc.) in .claude.json alongside centralstation.
                # force_overwrite=False: keep the CLI-managed volume token if it is newer.
                await asyncio.to_thread(
                    configure_claude_credentials, str(user.id),
                    _claude_creds.get("access_token", ""),
                    _claude_creds.get("refresh_token", ""),
                    _claude_creds.get("expires_at") or None,
                    extra_servers,
                )
        elif agent_type == "codex_cli":
            _codex_creds = await _load_agent_creds(db, user.id, "codex_cli")
            if _codex_creds:
                # Pass the same MCP server set Hermes gets (centralstation is added
                # by configure_codex_credentials itself; extra_servers = personal
                # connectors like VibeMK) so codex can use CentralStation tools.
                await asyncio.to_thread(
                    configure_codex_credentials, str(user.id),
                    _codex_creds.get("access_token", ""), extra_servers,
                )
    except Exception as exc:
        log.warning("Could not ensure userenv container for %s: %s — falling back to shared Hermes session", user.id, exc)

    target = _target_url(user.id)
    async with _internal_client(timeout=90.0) as client:
        r = await client.post(f"{target}/sessions", json=llm_payload)
    _check(r)
    data = r.json()
    sid = data["session_id"]

    # Use the caller's custom label (handoff host name) when provided. Otherwise
    # generate a label from the PostgreSQL session count — Hermes's in-memory
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
        agent_type=agent_type,
        ticket_connector_id=body.ticket_connector_id,
        ticket_issue_id=(body.ticket_issue_id or None),
        ticket_key=(body.ticket_key or None),
        context_hash=(body.context_hash or None),
    ))
    if body.ticket_connector_id and body.ticket_issue_id:
        work_session = (await db.execute(
            select(WorkSession)
            .where(
                WorkSession.user_id == user.id,
                WorkSession.jira_connector_id == body.ticket_connector_id,
                WorkSession.jira_issue_id == body.ticket_issue_id,
                WorkSession.status.notin_(("closed", "resolved")),
            )
            .order_by(WorkSession.updated_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if work_session and not work_session.computer_session_id:
            work_session.computer_session_id = sid
    await db.commit()
    log.info("Computer session %s created for user %s (label=%s, external_id=%s)",
             sid[:8], user.id, label, body.external_id or "-")
    return {
        **data,
        "label": label,
        "external_id": body.external_id or None,
        "agent_type": agent_type,
        "ticket_ref": ({
            "connector_id": str(body.ticket_connector_id),
            "issue_id": body.ticket_issue_id,
            "key": body.ticket_key,
        } if body.ticket_connector_id else None),
        "context_hash": body.context_hash or None,
        "has_activity_snapshot": False,
        "context_synced_at": None,
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
        "reused": False,
    }


@router.get("/sessions")
async def list_sessions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Return sessions from PostgreSQL (survives Hermes restarts)."""
    rows = (await db.execute(
        select(ComputerSession)
        .where(ComputerSession.user_id == user.id)
        .order_by(ComputerSession.last_activity_at.desc(), ComputerSession.created_at.desc())
    )).scalars().all()
    return [
        {
            "session_id": r.id,
            "label": r.label,
            "msg_count": r.msg_count,
            "created_at": r.created_at.isoformat(),
            "last_activity_at": r.last_activity_at.isoformat(),
            "external_id": r.external_id,
            "resolved": r.resolved,
            "agent_type": r.agent_type,
            "ticket_ref": ({
                "connector_id": str(r.ticket_connector_id),
                "issue_id": r.ticket_issue_id,
                "key": r.ticket_key,
            } if r.ticket_connector_id and r.ticket_issue_id else None),
            "context_hash": r.context_hash,
            "has_activity_snapshot": bool(r.ticket_activity_snapshot),
            "context_synced_at": r.context_synced_at.isoformat() if r.context_synced_at else None,
        }
        for r in rows
    ]


def _ticket_ref_payload(session: ComputerSession, detail: dict | None = None) -> dict:
    return {
        "connector_id": str(session.ticket_connector_id),
        "issue_id": session.ticket_issue_id,
        "key": (detail or {}).get("key") or session.ticket_key,
    }


async def _load_ticket_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    sid: str,
) -> ComputerSession:
    session = (await db.execute(
        select(ComputerSession).where(
            ComputerSession.id == sid,
            ComputerSession.user_id == user_id,
        )
    )).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")
    if not session.ticket_connector_id or not session.ticket_issue_id:
        raise HTTPException(status_code=422, detail="Session hat keinen eindeutigen Ticketbezug")
    return session


async def _load_ticket_connector(
    db: AsyncSession,
    user_id: uuid.UUID,
    connector_id: uuid.UUID,
) -> ConnectorConfig:
    connector = (await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.id == connector_id,
            ConnectorConfig.type.in_(("jira", "jira_sd")),
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
    )).scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=503, detail="Jira-Quelle nicht erreichbar")
    return connector


async def _fetch_ticket_detail(connector: ConnectorConfig, issue_id: str) -> dict:
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    jira = JiraConnector(
        base_url=connector.base_url,
        credentials=decrypt_credentials(connector.encrypted_credentials),
    )
    detail = await jira.get_issue_detail(issue_id)
    returned_id = str(detail.get("id") or "")
    if returned_id and returned_id != str(issue_id):
        raise ValueError("Jira lieferte eine abweichende Issue-ID")
    return detail


def _ticket_activity_payload(session: ComputerSession, detail: dict) -> dict:
    from app.services.ticket_activity import build_ticket_snapshot, diff_ticket_activity

    snapshot = build_ticket_snapshot(detail)
    activity = diff_ticket_activity(
        session.ticket_activity_snapshot,
        snapshot,
        detail,
        synced_at=session.context_synced_at or session.created_at,
    )
    return {
        "session_id": session.id,
        "ticket_ref": _ticket_ref_payload(session, detail),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "issue_updated_at": snapshot.get("issue_updated_at"),
        "snapshot": snapshot,
        **activity,
    }


@router.get("/ticket-activity")
async def list_ticket_activity(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    session_id: str | None = None,
    _: None = _ConsoleEnabled,
):
    """Check saved ticket sessions against their exact Jira source.

    The endpoint is read-only: activity remains unread until the explicit ack
    endpoint stores the observed snapshot after a successful agent response.
    """
    query = select(ComputerSession).where(
        ComputerSession.user_id == user.id,
        ComputerSession.ticket_connector_id.is_not(None),
        ComputerSession.ticket_issue_id.is_not(None),
    )
    if session_id:
        query = query.where(ComputerSession.id == session_id)
    sessions = (await db.execute(query.order_by(ComputerSession.created_at.asc()))).scalars().all()
    if session_id and not sessions:
        raise HTTPException(status_code=404, detail="Ticket-Session nicht gefunden")
    if not sessions:
        return []

    connector_ids = {session.ticket_connector_id for session in sessions}
    connectors = (await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.id.in_(connector_ids),
            ConnectorConfig.type.in_(("jira", "jira_sd")),
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user.id) | ConnectorConfig.owner_user_id.is_(None)),
        )
    )).scalars().all()
    connector_map = {connector.id: connector for connector in connectors}

    semaphore = asyncio.Semaphore(4)

    async def check(session: ComputerSession) -> dict:
        connector = connector_map.get(session.ticket_connector_id)
        if not connector:
            return {
                "session_id": session.id,
                "ticket_ref": _ticket_ref_payload(session),
                "state": "unavailable",
                "comment_change_count": 0,
                "new_comments": [],
                "edited_comments": [],
                "deleted_comment_ids": [],
                "field_changes": [],
                "ticket_changed": False,
                "error": "Jira-Quelle nicht erreichbar",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        try:
            async with semaphore:
                detail = await _fetch_ticket_detail(connector, session.ticket_issue_id)
            return _ticket_activity_payload(session, detail)
        except Exception as exc:  # one unavailable Jira must not hide other sessions
            log.warning("Ticket activity check failed for session %s: %s", session.id[:8], exc)
            return {
                "session_id": session.id,
                "ticket_ref": _ticket_ref_payload(session),
                "state": "unavailable",
                "comment_change_count": 0,
                "new_comments": [],
                "edited_comments": [],
                "deleted_comment_ids": [],
                "field_changes": [],
                "ticket_changed": False,
                "error": "Jira-Quelle nicht erreichbar",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    return await asyncio.gather(*(check(session) for session in sessions))


@router.post("/sessions/{sid}/ticket-activity/context")
async def ticket_activity_context(
    sid: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Build a fresh delta prompt and its exact acknowledgement snapshot."""
    from app.services.ticket_activity import (
        build_full_ticket_prompt,
        build_ticket_activity_prompt,
    )

    session = await _load_ticket_session(db, user.id, sid)
    connector = await _load_ticket_connector(db, user.id, session.ticket_connector_id)
    try:
        detail = await _fetch_ticket_detail(connector, session.ticket_issue_id)
    except Exception as exc:
        log.warning("Ticket activity context failed for session %s: %s", sid[:8], exc)
        raise HTTPException(status_code=503, detail="Jira-Quelle nicht erreichbar") from exc

    payload = _ticket_activity_payload(session, detail)
    if payload["state"] != "changed":
        return {**payload, "prompt": "", "context_hash": session.context_hash}

    full_prompt = build_full_ticket_prompt(detail, detail.get("key") or session.ticket_key)
    return {
        **payload,
        "prompt": build_ticket_activity_prompt(detail, payload),
        "context_hash": hashlib.sha256(full_prompt.encode("utf-8")).hexdigest(),
    }


class _AckTicketActivityBody(BaseModel):
    snapshot: dict
    context_hash: str | None = None


@router.post("/sessions/{sid}/ticket-activity/ack")
async def acknowledge_ticket_activity(
    sid: str,
    body: _AckTicketActivityBody,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Persist only the Jira snapshot that was successfully sent to the agent."""
    from app.services.ticket_activity import valid_ticket_snapshot

    session = await _load_ticket_session(db, user.id, sid)
    if not valid_ticket_snapshot(body.snapshot):
        raise HTTPException(status_code=422, detail="Ungültiger Ticket-Snapshot")
    if str(body.snapshot.get("issue_id") or "") != str(session.ticket_issue_id):
        raise HTTPException(status_code=409, detail="Snapshot gehört zu einem anderen Ticket")
    if len(json.dumps(body.snapshot, ensure_ascii=False)) > 256_000:
        raise HTTPException(status_code=413, detail="Ticket-Snapshot ist zu groß")

    session.ticket_activity_snapshot = body.snapshot
    session.context_synced_at = datetime.now(timezone.utc)
    if body.context_hash is not None:
        session.context_hash = body.context_hash[:64] or None
    await db.commit()
    return {
        "ok": True,
        "context_synced_at": session.context_synced_at.isoformat(),
        "context_hash": session.context_hash,
    }


class _UpdateSessionBody(BaseModel):
    # Re-point a reused handoff session at a new alert. Setting external_id
    # resets resolved so the "✓ GELÖST" button reappears for the new alert.
    external_id: str | None = None
    label: str | None = None
    context_hash: str | None = None


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
    if body.context_hash is not None:
        values["context_hash"] = body.context_hash[:64] or None
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
    # Delete from Hermes (best-effort — may already be gone after restart)
    try:
        async with _internal_client(timeout=10.0) as client:
            await client.delete(f"{_target_url(user.id)}/sessions/{sid}")
    except Exception as exc:
        log.debug("hermes delete %s: %s (ignored)", sid[:8], exc)

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

    # Write agents.md entry in the user workspace so the Werkbank IDE sees the link.
    await asyncio.to_thread(_append_agents_md, str(user.id), sid, cs.label)

    return {"id": str(ws.id), "already_linked": False}


def _append_agents_md(user_id: str, session_id: str, label: str) -> None:
    """Append a transfer entry to {workspace}/agents.md on the host."""
    import datetime
    from app.services.userenv_manager import workspace_dir

    ws_dir = workspace_dir(user_id)
    agents_md = os.path.join(ws_dir, "agents.md")
    today = datetime.date.today().isoformat()
    sid_short = session_id[:8]

    header_needed = not os.path.exists(agents_md)
    try:
        os.makedirs(ws_dir, exist_ok=True)
        with open(agents_md, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("# Agents Log\n\n"
                        "Automatisch gepflegt von Hermes. Enthält Session-Artefakte und Quelldateien.\n\n")
            f.write(f"## [{today}] {label} ({sid_short}…)\n")
            f.write(f"- Quelle: Hermes Computer-Session `{session_id}`\n")
            f.write(f"- In Werkbank übertragen: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n")
            f.write(f"- Workspace: `/root/workspaces/`\n\n")
        log.info("agents.md updated for user %s session %s", user_id, sid_short)
    except Exception as exc:
        log.warning("agents.md write failed for %s: %s", user_id, exc)


@router.get("/sessions/{sid}/history")
async def get_history(
    sid: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    from app.services.userenv_manager import ensure_container
    try:
        await asyncio.to_thread(ensure_container, str(user.id))
        await _reapply_ssh_if_recreated(db, user.id)
    except Exception as exc:
        log.warning("get_history: ensure_container failed for %s: %s", user.id, exc)

    try:
        async with _internal_client(timeout=35.0) as client:
            r = await client.get(f"{_target_url(user.id)}/sessions/{sid}/history")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        log.warning("get_history: container not reachable for %s: %s", user.id, exc)
        return []
    if r.status_code == 404:
        return []
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
    log.debug("proxy message → hermes session %s", sid[:8])

    # Ensure the per-user container is running. This is a no-op when it's already up
    # and auto-restarts it after a Docker restart or explicit docker rm.
    from app.services.userenv_manager import ensure_container as _ensure
    try:
        await asyncio.to_thread(_ensure, str(user.id))
        await _reapply_ssh_if_recreated(db, user.id)
    except Exception as exc:
        log.warning("send_message: ensure_container failed for %s: %s", user.id, exc)

    # Inject active LLM config into every message so Hermes can use it
    # when restoring a session after a container restart (env-var defaults are
    # not configured in the userenv container).
    try:
        from app.services.settings import get_active_llm_config, get_searxng_config, get_setting
        searxng = await get_searxng_config(db)
        # Admin toggle: show the model's reasoning in the session (default ON).
        show_reasoning = (await get_setting(db, "computer.show_reasoning") or "true") != "false"
        body_data = _json.loads(body)
        _msg_pref = (await db.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        )).scalar_one_or_none()
        _agent_type = getattr(_msg_pref, "computer_agent", None) or "hermes"
        # For Hermes sessions: prefer console-specific LLM config; fall back to
        # the global admin LLM config. Do not pass user_id here: user-scoped
        # personal LLM connectors would override the explicit "global" mode.
        if _agent_type == "hermes":
            llm = (await _get_console_llm_config(db, user.id)) or (await get_active_llm_config(db))
        else:
            llm = await get_active_llm_config(db, user_id=user.id)
        # For CLI agents, override llm_model with the user's stored CLI model preference.
        # userenv passes body.llm_model as --model flag to the CLI subprocess.
        cli_model: str | None = None
        if _agent_type in ("claude_cli", "codex_cli"):
            _cli_creds = await _load_agent_creds(db, user.id, _agent_type)
            cli_model = (_cli_creds or {}).get("model") or None

        body_data.update({
            "llm_base_url": llm.base_url or None,
            # CLI agents: use stored CLI model preference; do NOT fall back to
            # Hermes model (claude-sonnet-4-6 would break codex, o3 would break
            # claude). Hermes must receive its own LLM model so an existing
            # session is not re-initialized with an empty model on first message.
            "llm_model": (cli_model if _agent_type in ("claude_cli", "codex_cli") else llm.model) or None,
            "llm_api_key": llm.api_key or None,
            "llm_api_mode": llm.api_mode or "chat_completions",
            "searxng_url": searxng.base_url if searxng.is_configured else None,
            "llm_timeout_seconds": llm.timeout_seconds or None,
            "show_reasoning": show_reasoning,
            "agent_type": _agent_type,
        })
        body = _json.dumps(body_data).encode()
    except Exception as exc:
        log.debug("LLM config inject for message failed (non-fatal): %s", exc)

    async def stream_gen():
        async with _internal_client(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{_target_url(user.id)}/sessions/{sid}/message",
                content=body,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status_code >= 400:
                    err = await resp.aread()
                    log.warning("hermes %s for session %s: %s",
                                resp.status_code, sid[:8], err[:200])
                    msg = "Session nicht mehr vorhanden — bitte neue Session starten." if resp.status_code == 404 \
                        else f"Hermes-Fehler {resp.status_code}"
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
                    .values(
                        msg_count=ComputerSession.msg_count + 1,
                        last_activity_at=datetime.now(timezone.utc),
                    )
                )
                await fresh_db.commit()
        except Exception as exc:
            log.debug("msg_count bump for %s failed: %s", sid[:8], exc)

    asyncio.ensure_future(_bump_msg_count())

    return StreamingResponse(
        stream_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Whisper STT ────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe(
    request: Request,
    user: CurrentUser,
    _: None = _ConsoleEnabled,
):
    body = await request.body()
    content_type = request.headers.get("content-type", "application/octet-stream")
    async with _internal_client(timeout=60.0) as client:
        r = await client.post(
            f"{_target_url(user.id)}/transcribe",
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


# ── Container management ───────────────────────────────────────────

class _WriteApprovalBody(BaseModel):
    minutes: int = 15


@router.get("/write-approval")
async def get_write_approval_state(user: CurrentUser, _: None = _ConsoleEnabled):
    """Current state of the console's write window."""
    from app.services.userenv_manager import get_write_approval
    granted = await asyncio.to_thread(get_write_approval, str(user.id))
    return {"active": bool(granted), "approval": granted}


@router.post("/write-approval", status_code=201)
async def grant_write_approval(
    body: _WriteApprovalBody,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Open a time-limited write window for this user's console agent.

    The in-container PreToolUse guard blocks system-modifying commands and cannot see
    the conversation — deliberately, since the agent controls that conversation and
    could otherwise talk itself into permission. This endpoint is the only consent
    channel: it writes a root-owned marker the agent (running as yolo) cannot forge.
    """
    from app.models.audit import AuditLog
    from app.services.userenv_manager import ensure_container, set_write_approval

    await asyncio.to_thread(ensure_container, str(user.id))
    try:
        approval = await asyncio.to_thread(
            set_write_approval, str(user.id), body.minutes, user.email
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Schreibfreigabe fehlgeschlagen: {exc}")

    # Security-relevant: record who opened write access to production systems.
    db.add(AuditLog(action="console_write_approval_granted", resource_type="userenv",
                    resource_id=str(user.id), user_id=user.id,
                    old_value=None, new_value={"minutes": body.minutes,
                                               "expires_at": approval["expires_at_iso"]}))
    await db.commit()
    log.info("console write approval granted for %s (%d min)", user.email, body.minutes)
    return {"active": True, "approval": approval}


@router.delete("/write-approval", status_code=204)
async def revoke_write_approval(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: None = _ConsoleEnabled,
):
    """Close the write window immediately."""
    from app.models.audit import AuditLog
    from app.services.userenv_manager import clear_write_approval

    try:
        await asyncio.to_thread(clear_write_approval, str(user.id))
    except Exception as exc:  # noqa: BLE001
        log.warning("revoke write approval failed for %s: %s", user.id, exc)
    db.add(AuditLog(action="console_write_approval_revoked", resource_type="userenv",
                    resource_id=str(user.id), user_id=user.id))
    await db.commit()


@router.post("/userenv/restart", status_code=202)
async def restart_userenv(
    user: CurrentUser,
    _: None = _ConsoleEnabled,
):
    """Restart the per-user Hermes container.

    Call this after changing SSH settings, MCP connectors, or LLM settings
    so that all in-container daemons pick up the new configuration.
    Note: all in-memory Hermes sessions are lost on restart.
    """
    from app.services.userenv_manager import container_name as _cname
    import docker as _docker

    def _do_restart() -> bool:
        try:
            cli = _docker.from_env()
            c = cli.containers.get(_cname(str(user.id)))
            c.restart(timeout=15)
            return True
        except _docker.errors.NotFound:
            return False

    found = await asyncio.to_thread(_do_restart)
    if not found:
        return {"restarted": False, "info": "Kein laufender Container gefunden"}
    log.info("userenv container restarted for user %s", user.id)
    return {"restarted": True}


# ── Helpers ────────────────────────────────────────────────────────

def _check(r: httpx.Response) -> None:
    if r.status_code >= 400:
        log.warning("Hermes returned %s: %s", r.status_code, r.text[:200])
        raise HTTPException(r.status_code, f"Hermes-Fehler: {r.text[:200]}")
