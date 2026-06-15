"""Werkbank Web-IDE API — per-user code-server orchestration + git workspace.

- GET  /api/ide/authz                       nginx auth_request gate (cookie-based)
- POST /api/ide/session/ensure              ensure the user's container, set cookie
- POST /api/ide/workspace/{wsid}/provision  clone the WorkSession's repo (PAT) + open

code-server runs with --auth none and no published port; nginx only proxies /ide/
after authz returns 2xx. The cs_ide_token cookie (type "ide") is the credential.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
    resp.set_cookie(
        COOKIE_NAME, token,
        max_age=12 * 3600, httponly=True, secure=True, samesite="strict", path="/ide",
    )


@router.get("/authz")
async def ide_authz(request: Request, response: Response):
    """For nginx auth_request. Validates the cs_ide_token cookie and that the
    requested /ide/<uid>/ matches the token's user. Returns X-IDE-Upstream."""
    token = request.cookies.get(COOKIE_NAME)
    payload = decode_token(token) if token else {}
    if not payload or payload.get("type") != "ide":
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


async def _agent_tokens(db: AsyncSession) -> tuple[str | None, str | None]:
    """Pull the Claude + Codex OAuth tokens (auto-refreshed) so the IDE terminal's
    CLI agents are authenticated without a browser OAuth flow. Best-effort."""
    claude = codex = None
    try:
        from app.api.oauth_providers import get_claude_access_token, get_codex_access_token
        claude = await get_claude_access_token(db)
        codex = await get_codex_access_token(db)
    except Exception as e:
        log.debug("agent token fetch failed (non-fatal): %s", e)
    return claude, codex


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
    claude, codex = await _agent_tokens(db)
    try:
        await asyncio.to_thread(ide_manager.ensure_container, uid, claude, codex)
    except Exception as e:
        log.warning("ide ensure failed for %s: %s", uid, e)
        raise HTTPException(503, f"IDE could not be started: {e}") from e
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

    claude, codex = await _agent_tokens(db)
    try:
        await asyncio.to_thread(ide_manager.ensure_container, uid, claude, codex)
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
