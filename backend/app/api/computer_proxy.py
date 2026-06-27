"""Computer Console proxy — forwards /api/computer/* to the Hermes service in the userenv container.

Adds JWT authentication and checks the computer_console_enabled preference
before forwarding any request. SSE streaming is passed through transparently.
The active LLM config (from CentralStation settings) is injected at session
creation so Hermes always uses the same model as the rest of CentralStation.
"""
from __future__ import annotations

import asyncio
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

def _internal_client(**kwargs) -> httpx.AsyncClient:
    """httpx client for intra-Docker requests (bypasses HTTP_PROXY env var)."""
    return httpx.AsyncClient(trust_env=False, **kwargs)


def _target_url(user_id: str) -> str:
    """Return the per-user Hermes container URL (http://cs-userenv-{uid}:8001)."""
    from app.services.userenv_manager import hermes_url
    return hermes_url(str(user_id))


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
    """Load stored CLI agent credentials (claude_cli or codex_cli connector)."""
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
    return _dec(conn.encrypted_credentials)


async def _upsert_agent_connector(
    db: AsyncSession, user_id, agent_type: str, creds: dict
) -> None:
    """Upsert a ConnectorConfig row for the given CLI agent type."""
    from sqlalchemy import select as _sel
    from app.models.connector import ConnectorConfig
    from app.core.security import encrypt_credentials as _enc
    res = await db.execute(
        _sel(ConnectorConfig).where(
            ConnectorConfig.type == agent_type,
            ConnectorConfig.owner_user_id == user_id,
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
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
        if not body.access_token:
            raise HTTPException(400, "access_token erforderlich für claude_cli")
        await _upsert_agent_connector(db, user.id, "claude_cli", {
            "access_token": body.access_token,
            "refresh_token": body.refresh_token or "",
            "expires_at": body.expires_at or "",
        })
        await asyncio.to_thread(ensure_container, str(user.id))
        await asyncio.to_thread(
            configure_claude_credentials,
            str(user.id), body.access_token, body.refresh_token or "", body.expires_at,
        )

    elif body.agent == "codex_cli":
        if not body.access_token:
            raise HTTPException(400, "access_token erforderlich für codex_cli")
        await _upsert_agent_connector(db, user.id, "codex_cli", {
            "access_token": body.access_token,
            "refresh_token": body.refresh_token or "",
        })
        await asyncio.to_thread(ensure_container, str(user.id))
        await asyncio.to_thread(configure_codex_credentials, str(user.id), body.access_token)

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
    "o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini",
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
]
_CODEX_EXCLUDE = ("embedding", "tts", "whisper", "dall-e", "babbage", "davinci", "ada", "curie")


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
        return {"models": fallback, "source": "static", "current_model": current_model}

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
                    return {"models": sorted(models), "source": "api", "current_model": current_model}
        else:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if r.status_code == 200:
                models = sorted([
                    m["id"] for m in r.json().get("data", [])
                    if not any(x in m["id"] for x in _CODEX_EXCLUDE)
                    and (m["id"].startswith("gpt-") or m["id"].startswith("o")
                         or m["id"].startswith("codex"))
                ])
                if models:
                    return {"models": models, "source": "api", "current_model": current_model}
    except Exception as exc:
        log.debug("Model fetch for %s failed: %s", provider, exc)

    return {"models": fallback, "source": "static", "current_model": current_model}


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
    creds["model"] = body.model
    conn.encrypted_credentials = _enc(creds)
    await db.commit()

    log.info("CLI model set to '%s' for %s / user %s", body.model, agent_type, user.id)
    return {"status": "saved", "model": body.model}


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
    return {
        "configured": True,
        "api_mode": creds.get("api_mode") or "chat_completions",
        "model": creds.get("model") or "",
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

    creds = {
        "api_mode": body.api_mode,
        "model": body.model,
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


@router.post("/sessions", status_code=201)
async def create_session(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: _CreateSessionBody = _CreateSessionBody(),
    _: None = _ConsoleEnabled,
):
    """Create a new Hermes session, persist metadata in PostgreSQL."""
    from app.services.settings import get_active_llm_config, get_searxng_config
    from app.models.connector import ConnectorConfig
    _ssh_creds: dict | None = None
    extra_servers: dict = {}  # personal MCP connectors; defined here so it survives
                              # an early exception in the LLM-config block below.
    try:
        # For Hermes sessions: prefer console-specific LLM config; fall back to global.
        # CLI sessions (claude_cli/codex_cli) use their own OAuth tokens — LLM config irrelevant.
        _agent_pref = (await db.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        )).scalar_one_or_none()
        _agent_type = getattr(_agent_pref, "computer_agent", None) or "hermes"
        if _agent_type == "hermes":
            llm = (await _get_console_llm_config(db, user.id)) or (await get_active_llm_config(db, user_id=user.id))
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
            _claude_creds = await _load_agent_creds(db, user.id, "claude_cli")
            if _claude_creds:
                # Pass extra_servers so configure_claude_credentials registers all personal
                # MCP connectors (VibeMK, AWX-NG, etc.) in .claude.json alongside centralstation.
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
    ))
    await db.commit()
    log.info("Computer session %s created for user %s (label=%s, external_id=%s)",
             sid[:8], user.id, label, body.external_id or "-")
    return {**data, "label": label, "external_id": body.external_id or None, "agent_type": agent_type}


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
            "agent_type": r.agent_type,
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
    _: None = _ConsoleEnabled,
):
    from app.services.userenv_manager import ensure_container
    try:
        await asyncio.to_thread(ensure_container, str(user.id))
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
        # For Hermes sessions: prefer console-specific LLM config; fall back to global.
        if _agent_type == "hermes":
            llm = (await _get_console_llm_config(db, user.id)) or (await get_active_llm_config(db, user_id=user.id))
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
            "llm_model": cli_model or llm.model or None,
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
                    .values(msg_count=ComputerSession.msg_count + 1)
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
