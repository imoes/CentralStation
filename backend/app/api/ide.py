"""Werkbank Web-IDE API — per-user code-server orchestration + git workspace.

- GET  /api/ide/authz                       nginx auth_request gate (cookie-based)
- POST /api/ide/session/ensure              ensure the user's container, set cookie
- POST /api/ide/workspace/{wsid}/provision  clone the WorkSession's repo (PAT) + open

code-server runs with --auth none and no published port; nginx only proxies /ide/
after authz returns 2xx. The cs_ide_token cookie (type "ide") is the credential.
For /ws WebSocket connections (Claude Code extension) the cs_ide_token cookie is
not sent (path="/ide" mismatch); the endpoint also accepts the CentralStation access
token extracted from the X-Original-URI query string as a fallback.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import pathlib
import re
import uuid
import zipfile
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.core.security import create_ide_token, decode_token
from app.models.workflow import WorkSession
from app.services import ide_manager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/ide", tags=["ide"])

_ALLOWED_ROLES = {"admin", "sysadmin"}
_URI_UID = re.compile(r"^/ide/([^/]+)/")
COOKIE_NAME = "cs_ide_token"


def _set_ide_cookie(resp: Response, user_id: str) -> None:
    token = create_ide_token({"sub": user_id})
    # secure=False: nginx listens HTTP-only (port 80) internally; browsers block
    # Secure cookies over plain HTTP so the auth_request gate would always fail.
    resp.set_cookie(
        COOKIE_NAME, token,
        max_age=12 * 3600, httponly=True, secure=False, samesite="lax", path="/ide",
    )


@router.get("/authz")
async def ide_authz(request: Request, response: Response):
    """For nginx auth_request. Validates the cs_ide_token cookie and that the
    requested /ide/<uid>/ matches the token's user. Returns X-IDE-Upstream.

    Fallback: for /ws WebSocket requests (Claude Code extension) the browser
    sends the CentralStation access token as ?token= in the URL instead of the
    IDE cookie (path mismatch). We extract it from X-Original-URI."""
    token = request.cookies.get(COOKIE_NAME)
    payload = decode_token(token) if token else {}
    if not payload or payload.get("type") != "ide":
        # Fallback: extract ?token= from the original request URI
        original_uri = request.headers.get("X-Original-URI", "")
        qs = parse_qs(urlparse(original_uri).query)
        uri_token = qs.get("token", [None])[0]
        payload = (decode_token(uri_token) or {}) if uri_token else {}
        if payload.get("type") not in ("access", "ide"):
            raise HTTPException(401, "IDE auth required")
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(401, "Invalid IDE token")

    original_uri = request.headers.get("X-Original-URI", "")
    m = _URI_UID.match(original_uri)
    if m and m.group(1) != uid:
        raise HTTPException(403, "Workspace does not belong to you")

    ide_manager.touch(uid)
    return Response(status_code=200, headers={"X-IDE-Upstream": ide_manager.upstream(uid)})


_IDE_EXT_KNOWN = {
    "anthropic.claude-code": {"type": "claude-code", "name": "Claude Code"},
    "github.copilot-chat":   {"type": "copilot-chat", "name": "GitHub Copilot Chat"},
    "continue.continue":     {"type": "continue",     "name": "Continue (OpenAI)"},
}


@router.get("/extensions")
async def list_ide_extensions(user: CurrentUser):
    """Return AI coding extensions installed in the user's code-server.

    Reads the host-side bind-mount of /root/.local/share/code-server/extensions/
    so no docker exec is needed.  Returns an empty list when the container has
    never been started (directory absent) — the frontend treats that as
    'no extensions found'."""
    uid = str(user.id)
    ext_dir = os.path.join(ide_manager.vscode_dir(uid), "extensions")
    found: list[dict] = []
    try:
        if os.path.isdir(ext_dir):
            for entry in os.listdir(ext_dir):
                for prefix, meta in _IDE_EXT_KNOWN.items():
                    if entry.startswith(prefix):
                        found.append({"id": entry, **meta})
                        break
    except Exception as exc:
        log.debug("extension list failed for %s: %s", uid, exc)
    return {"extensions": found}


class OpenChatRequest(BaseModel):
    session_id: str
    extension_type: str = "none"  # 'claude-code' | 'continue' | 'copilot-chat' | 'none'
    session_label: str
    messages: list[dict]          # [{"role": "user"|"assistant", "text": str}]


@router.post("/open-chat")
async def open_chat_in_ide(
    body: OpenChatRequest,
    user: CurrentUser,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Write the Hermes session as a markdown context file into the workspace,
    set the IDE cookie, and return the code-server URL that opens the file."""
    uid = str(user.id)
    try:
        await asyncio.to_thread(ide_manager.ensure_container, uid)
    except Exception as e:
        raise HTTPException(503, f"IDE could not be started: {e}") from e

    filename = f"hermes-{body.session_id[:8]}.md"
    ws_dir = ide_manager.workspace_dir(uid)
    os.makedirs(ws_dir, exist_ok=True)

    from datetime import datetime, timezone

    last_asst = next(
        (m.get("text", "").strip() for m in reversed(body.messages) if m.get("role") == "assistant"),
        "",
    )
    summary = (last_asst[:600] + " …") if len(last_asst) > 600 else last_asst
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# {body.session_label}",
        "",
        f"> Exportiert aus Hermes · {now}",
        "",
        "## Zusammenfassung",
        "",
        summary or "_Keine Assistenz-Antwort vorhanden._",
        "",
        "## Aktionsschritte",
        "",
        "- [ ] ",
        "",
        "## Anmerkungen",
        "",
        "",
        "---",
        "",
        "<details>",
        "<summary>Vollständiger Hermes-Dialog</summary>",
        "",
    ]
    for msg in body.messages:
        role = "**▶ Nutzer**" if msg.get("role") == "user" else "**◎ Hermes**"
        lines.append(f"### {role}\n{(msg.get('text') or '').strip()}\n")
    lines += ["</details>", ""]

    filepath_host = os.path.join(ws_dir, filename)
    with open(filepath_host, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    # CLAUDE.md — auto-discovered by Claude Code as project context on every chat.
    # Keeps only the latest Hermes session summary so the file stays concise.
    claude_lines = [
        f"# {body.session_label}",
        "",
        f"> Übertragen aus Hermes · {now}",
        "",
        "## Aktueller Stand",
        "",
        summary or "_Kein Assistenz-Kontext vorhanden._",
        "",
        "## Aktionsschritte",
        "",
        "- [ ] ",
        "",
        f"_Vollständiger Dialog: `{filename}` im Workspace-Root_",
        "",
    ]
    with open(os.path.join(ws_dir, "CLAUDE.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(claude_lines))

    filepath_container = f"{ide_manager.WORKSPACES_DIR}/{filename}"
    _set_ide_cookie(response, uid)
    return {
        "ide_url":  f"/ide/{uid}/?file={filepath_container}",
        "filepath": filepath_container,
        "filename": filename,
    }


async def _inject_cli_credentials(db: AsyncSession, uid: str) -> None:
    """Inject Claude/Codex credentials into the running userenv container.

    Mirrors the credential injection in computer_proxy.create_session so that
    opening the Werkbank IDE also refreshes credentials — not only Console sessions.
    """
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.userenv_manager import configure_claude_credentials, configure_codex_credentials
    from sqlalchemy import select as _sel
    import uuid as _uuid

    user_uuid = _uuid.UUID(uid)

    async def _load_creds(agent_type: str) -> dict | None:
        res = await db.execute(
            _sel(ConnectorConfig).where(
                ConnectorConfig.type == agent_type,
                ConnectorConfig.owner_user_id == user_uuid,
            ).limit(1)
        )
        conn = res.scalar_one_or_none()
        return decrypt_credentials(conn.encrypted_credentials) if conn else None

    claude_creds = await _load_creds("claude_cli")
    if claude_creds:
        try:
            await asyncio.to_thread(
                configure_claude_credentials, uid,
                claude_creds.get("access_token", ""),
                claude_creds.get("refresh_token", ""),
                claude_creds.get("expires_at") or None,
                {},
            )
            log.info("ide ensure: injected claude_cli credentials for %s", uid)
        except Exception as exc:
            log.warning("ide ensure: configure_claude_credentials failed for %s: %s", uid, exc)

    codex_creds = await _load_creds("codex_cli")
    if codex_creds:
        try:
            await asyncio.to_thread(
                configure_codex_credentials, uid,
                codex_creds.get("access_token", ""),
                {},
            )
            log.info("ide ensure: injected codex_cli credentials for %s", uid)
        except Exception as exc:
            log.warning("ide ensure: configure_codex_credentials failed for %s: %s", uid, exc)


@router.post("/session/ensure")
async def ensure_session(
    user: CurrentUser,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Ensure the user's code-server container is running; set the IDE cookie."""
    if user.role not in _ALLOWED_ROLES:
        raise HTTPException(403, "IDE requires admin or sysadmin role")
    uid = str(user.id)
    try:
        await asyncio.to_thread(ide_manager.ensure_container, uid)
    except Exception as e:
        log.warning("ide ensure failed for %s: %s", uid, e)
        raise HTTPException(503, f"IDE could not be started: {e}") from e
    # Re-inject CLI agent credentials so Claude/Codex work in code-server after
    # container restart (without needing a Console session to trigger the injection).
    await _inject_cli_credentials(db, uid)

    _set_ide_cookie(response, uid)
    return {"ide_base": f"/ide/{uid}/"}


async def _load_gitlab(user, db: AsyncSession):
    """Resolve the user's GitLab connector (per-user or shared) → (base_url, token)."""
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "gitlab",
            ConnectorConfig.enabled.is_(True),
        ).where(
            (ConnectorConfig.owner_user_id == user.id) | (ConnectorConfig.owner_user_id.is_(None))
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        return None, None, None
    creds = decrypt_credentials(cfg.encrypted_credentials)
    return cfg.base_url, creds.get("token", ""), creds


@router.post("/workspace/{work_session_id}/provision")
async def provision_workspace(
    work_session_id: uuid.UUID,
    user: CurrentUser,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Ensure the container, clone the WorkSession's GitLab repo with the user's
    PAT (server-side), checkout the branch, and return the iframe URL."""
    if user.role not in _ALLOWED_ROLES:
        raise HTTPException(403, "IDE requires admin or sysadmin role")
    uid = str(user.id)
    s = (await db.execute(
        select(WorkSession).where(WorkSession.id == work_session_id, WorkSession.user_id == user.id)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "WorkSession not found")

    try:
        await asyncio.to_thread(ide_manager.ensure_container, uid)
    except Exception as e:
        raise HTTPException(503, f"IDE could not be started: {e}") from e

    folder = ide_manager.WORKSPACES_DIR
    repo_dir = None

    if s.gitlab_project_id:
        base_url, token, _creds = await _load_gitlab(user, db)
        if not token:
            raise HTTPException(503, "No GitLab connector / token for this user")
        from app.services.connectors.gitlab import GitLabConnector
        gl = GitLabConnector(base_url=base_url, credentials={"token": token})
        try:
            proj = await gl.get_project(s.gitlab_project_id)
        except Exception as e:
            raise HTTPException(502, f"GitLab project lookup failed: {e}") from e

        path_ns = proj.get("path_with_namespace") or proj.get("path") or str(s.gitlab_project_id)
        repo_dir = path_ns.split("/")[-1]
        host = base_url.split("://", 1)[-1].rstrip("/")
        branch = s.gitlab_branch or proj.get("default_branch") or "main"

        # Clone + credentials happen inside the container (token via env, not argv).
        script = (
            "set -e; "
            "git config --global credential.helper store; "
            'printf "https://oauth2:%s@%s\\n" "$GL_TOKEN" "$GL_HOST" > /root/.git-credentials; '
            "chmod 600 /root/.git-credentials; "
            'git config --global user.name "$GL_USER"; '
            'git config --global user.email "$GL_EMAIL"; '
            f"cd {ide_manager.WORKSPACES_DIR}; "
            f'if [ ! -d "{repo_dir}/.git" ]; then git clone "https://{host}/{path_ns}.git" "{repo_dir}"; fi; '
            f'cd "{repo_dir}"; git fetch --all -q || true; '
            f'git checkout "{branch}" 2>/dev/null || git checkout -b "{branch}"'
        )
        env = {
            "GL_TOKEN": token,
            "GL_HOST": host,
            "GL_USER": (user.full_name or "CentralStation"),
            "GL_EMAIL": (user.email or "noreply@ippen.media"),
        }
        code, out = await asyncio.to_thread(ide_manager.exec_sh, uid, script, env)
        if code != 0:
            log.warning("ide provision git failed (%s): %s", code, out[-500:])
            raise HTTPException(502, f"git clone/checkout failed: {out[-300:]}")
        folder = f"{ide_manager.WORKSPACES_DIR}/{repo_dir}"
        s.workspace_path = folder
        await db.commit()

    _set_ide_cookie(response, uid)
    return {"ide_url": f"/ide/{uid}/?folder={folder}", "workspace_path": folder, "repo_dir": repo_dir}


# ── Project → Werkbank handoff ─────────────────────────────────────────────

class OpenProjectRequest(BaseModel):
    project_id: str


@router.post("/open-project")
async def open_project_in_ide(
    body: OpenProjectRequest,
    user: CurrentUser,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Write a project plan into the IDE workspace and return the code-server URL.

    Creates:
      project-plan.puml   — PlantUML source (network diagram), readable by PlantUML extension
      project-plan.md     — Human-readable plan
      .centralstation/project.json — Machine-readable project context (id, steps, deps)
      CLAUDE.md           — Instructions for Claude/Codex to use MCP tools for all writes
    """
    import json as _json
    from app.services import project_plantuml
    import app.services.project_service as svc

    uid = str(user.id)
    try:
        await asyncio.to_thread(ide_manager.ensure_container, uid)
    except Exception as e:
        raise HTTPException(503, f"IDE could not be started: {e}") from e

    graph = await svc.get_project_graph(db, body.project_id)
    proj = graph.project

    ws_dir = ide_manager.workspace_dir(uid)
    os.makedirs(ws_dir, exist_ok=True)
    cs_dir = os.path.join(ws_dir, ".centralstation")
    os.makedirs(cs_dir, exist_ok=True)

    step_dicts = [s.model_dump(mode="json") for s in graph.steps]
    dep_dicts = [d.model_dump(mode="json") for d in graph.deps]

    # project-plan.puml
    puml_network = project_plantuml.render_network(proj.name, step_dicts, dep_dicts)
    with open(os.path.join(ws_dir, "project-plan.puml"), "w", encoding="utf-8") as f:
        f.write(puml_network)

    # project-plan.md
    md = project_plantuml.render_markdown(proj.name, proj.description, step_dicts, dep_dicts)
    with open(os.path.join(ws_dir, "project-plan.md"), "w", encoding="utf-8") as f:
        f.write(md)

    # .centralstation/project.json
    project_ctx = {
        "project_id": body.project_id,
        "name": proj.name,
        "steps": step_dicts,
        "deps": dep_dicts,
    }
    with open(os.path.join(cs_dir, "project.json"), "w", encoding="utf-8") as f:
        _json.dump(project_ctx, f, indent=2, default=str)

    # CLAUDE.md — instructs the AI to use MCP for all writes
    step_ids_preview = "\n".join(
        f"  - {s.get('title', '?')} → id: {s.get('id', '?')}"
        for s in step_dicts[:10]
    )
    claude_md = f"""# CentralStation Projekt: {proj.name}

Dieses Workspace ist mit CentralStation-Projekt `{body.project_id}` verknüpft.

## WICHTIG: Nur MCP für Änderungen am Plan

Lese und ändere den Live-Plan **ausschließlich** über die `centralstation`-MCP-Tools.
Die `.puml`-Datei ist nur die generierte Anzeige, nicht die Wahrheit.

## Verfügbare MCP-Tools (prefix: `centralstation`)

- `cs_get_project_plan(project_id)` — aktuellen Plan abrufen
- `cs_add_step(project_id, title, description, jira_issue_type, duration_days, depends_on, parent_step_id)` — Schritt hinzufügen
- `cs_update_step(project_id, step_id, title?, description?, duration_days?)` — Schritt aktualisieren
- `cs_set_step_status(project_id, step_id, status)` — Status setzen (pending|in_progress|done)
- `cs_add_dependency(project_id, step_id, depends_on_step_id)` — Abhängigkeit hinzufügen
- `cs_remove_dependency(project_id, dep_id)` — Abhängigkeit entfernen

## Projekt-ID

Immer `project_id="{body.project_id}"` übergeben.

## Aktuelle Schritte (Kurzübersicht)

{step_ids_preview}

Vollständiger Plan: `cs_get_project_plan("{body.project_id}")`
"""
    with open(os.path.join(ws_dir, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(claude_md)

    _set_ide_cookie(response, uid)
    return {
        "ide_url": f"/ide/{uid}/?folder={ide_manager.WORKSPACES_DIR}",
        "project_id": body.project_id,
    }


# ── Werkbank File-Manager endpoints ──────────────────────────────────────────
# Called by the cs-filemanager VS Code extension from *inside* the container via
# the Docker network (http://backend:8000). Auth: X-CS-UID header validated by
# checking that a cs-userenv-{uid} container is actually running.

def _fm_docker_check(uid: str) -> None:
    """403 if no cs-userenv-{uid} container is running."""
    import docker as _docker
    try:
        _docker.from_env().containers.get(ide_manager.container_name(uid))
    except Exception:
        raise HTTPException(403, "Container not running for uid")


def _fm_validate(request: Request) -> str:
    """Return user_id from X-CS-UID header; 403 if container not running."""
    uid = request.headers.get("X-CS-UID", "").strip()
    if not uid:
        raise HTTPException(403, "X-CS-UID header required")
    _fm_docker_check(uid)
    return uid


def _fm_safe_path(uid: str, relpath: str) -> pathlib.Path:
    """Resolve relpath inside workspace_dir(uid); raise 400 on path traversal."""
    base   = pathlib.Path(ide_manager.workspace_dir(uid)).resolve()
    target = (base / relpath).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(400, "Invalid path")
    return target


@router.get("/workspace/download")
async def workspace_download(request: Request, path: str = "", uid: str = ""):
    """Download a file or a folder (as ZIP). Called by cs-filemanager extension."""
    if not uid:
        uid = _fm_validate(request)
    else:
        _fm_docker_check(uid)

    target = _fm_safe_path(uid, path)
    if not target.exists():
        raise HTTPException(404, f"Not found: {path}")

    if target.is_file():
        filename = target.name

        def _iter():
            with open(target, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk

        return StreamingResponse(
            _iter(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        # Folder → stream as ZIP
        foldername = target.name

        def _zip_stream():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in target.rglob("*"):
                    if fp.is_file():
                        zf.write(fp, fp.relative_to(target.parent))
            buf.seek(0)
            while chunk := buf.read(65536):
                yield chunk

        return StreamingResponse(
            _zip_stream(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{foldername}.zip"'},
        )


@router.post("/workspace/extract")
async def workspace_extract(request: Request, path: str = "", zipname: str = "", uid: str = ""):
    """Extract a ZIP file (sent as body) into workspace path. Called by cs-filemanager."""
    if not uid:
        uid = _fm_validate(request)
    else:
        _fm_docker_check(uid)

    target_dir = _fm_safe_path(uid, path)
    target_dir.mkdir(parents=True, exist_ok=True)

    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty body")

    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            for member in zf.infolist():
                # Skip path traversal members
                dest = (target_dir / member.filename).resolve()
                if not str(dest).startswith(str(target_dir.resolve())):
                    continue
                if member.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, f"Invalid ZIP: {e}")

    return {"extracted": True, "path": path}
