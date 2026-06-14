"""KI-basierter Playbook-Author: generiert Ansible-YAML und publiziert in GitLab+AWX."""
from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any

log = logging.getLogger(__name__)


async def author_playbook(
    task_description: str,
    context: str,
    db: Any,
    created_by: _uuid.UUID | None = None,
) -> "PlaybookDraft | None":
    """LLM generates an Ansible playbook YAML for a management task.

    Returns a `PlaybookDraft` in status 'drafted', or None on failure.
    """
    from app.models.remediation import PlaybookDraft
    from app.services.settings import get_llm_config
    from app.services.llm_client import generate_text

    llm_cfg = await get_llm_config(db)
    if not llm_cfg.is_configured:
        return None

    prompt = (
        "You are an Ansible automation engineer. Write a complete, valid Ansible playbook YAML "
        "for the following task. Include a clear 'name' for the play, use ansible.builtin.* FQCNs, "
        "and add variables (vars:) for any host-specific parameters so the playbook can be launched "
        "via AWX with extra_vars.\n\n"
        f"Task: {task_description}\n"
        f"Context: {context}\n\n"
        "Respond ONLY with the raw YAML (no markdown fences, no explanation). "
        "After the YAML add a JSON metadata block on the LAST line, prefixed with '# META:' like:\n"
        '# META: {"title": "<short name>", "target": "<linux|windows|network|generic>", "description": "<one line>"}'
    )

    try:
        raw = await generate_text(
            llm_cfg,
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_output_tokens=2000,
        )
    except Exception as exc:
        log.warning("playbook_author: LLM failed: %s", exc)
        return None

    # Split YAML from META line
    meta = {"title": task_description[:100], "target": "generic", "description": ""}
    lines = raw.strip().splitlines()
    yaml_lines = []
    for line in lines:
        if line.startswith("# META:"):
            try:
                meta = json.loads(line[len("# META:"):].strip())
            except Exception:
                pass
        else:
            yaml_lines.append(line)
    yaml_content = "\n".join(yaml_lines).strip()

    draft = PlaybookDraft(
        title=meta.get("title", task_description[:100]),
        yaml=yaml_content,
        target=meta.get("target", "generic"),
        description=meta.get("description", ""),
        status="drafted",
        created_by=created_by,
    )
    db.add(draft)
    await db.flush()
    log.info("playbook_author: drafted '%s'", draft.title)
    return draft


async def publish_playbook(
    draft_id: _uuid.UUID,
    db: Any,
    approved_by: _uuid.UUID | None = None,
) -> dict:
    """Commit playbook to GitLab, trigger AWX project sync, create job template.

    Returns a dict with status information.
    """
    from sqlalchemy import select
    from app.models.remediation import PlaybookDraft
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.gitlab import GitLabConnector
    from app.services.connectors.awx import AWXConnector
    from app.services.settings import get_setting

    draft = (await db.execute(select(PlaybookDraft).where(PlaybookDraft.id == draft_id))).scalar_one_or_none()
    if not draft:
        return {"error": "Draft not found"}
    if draft.status != "drafted":
        return {"error": f"Draft is already '{draft.status}'"}

    result = {}

    # ── 1. Commit to GitLab ───────────────────────────────────────
    gl_cfg = (await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "gitlab",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )).scalar_one_or_none()

    if gl_cfg:
        project_id = (await get_setting(db, "awx.playbook_repo_project")) or gl_cfg.encrypted_credentials  # fallback
        try:
            project_id = (await get_setting(db, "awx.playbook_repo_project"))
            branch = (await get_setting(db, "awx.playbook_repo_branch")) or "main"
            if project_id:
                creds = decrypt_credentials(gl_cfg.encrypted_credentials)
                gl = GitLabConnector(base_url=gl_cfg.base_url, credentials=creds)
                safe_name = draft.title.lower().replace(" ", "_")[:50] + ".yml"
                await gl.create_or_update_file(
                    project_id=project_id,
                    path=f"centralstation/{safe_name}",
                    branch=branch,
                    content=draft.yaml,
                    message=f"[CentralStation] Add playbook: {draft.title}",
                )
                result["gitlab"] = f"committed centralstation/{safe_name} to {branch}"
        except Exception as exc:
            log.warning("publish_playbook: GitLab commit failed: %s", exc)
            result["gitlab_error"] = str(exc)

    # ── 2. AWX project sync + create job template ─────────────────
    awx_cfg = (await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "awx",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )).scalar_one_or_none()

    if awx_cfg:
        try:
            creds = decrypt_credentials(awx_cfg.encrypted_credentials)
            awx = AWXConnector(base_url=awx_cfg.base_url, credentials=creds)
            project_id = awx.project_id
            if project_id:
                await awx.project_update(project_id)
                # Create job template
                safe_name = draft.title.lower().replace(" ", "_")[:50] + ".yml"
                tmpl = await awx.create_job_template(
                    name=f"[CS] {draft.title}",
                    playbook=f"centralstation/{safe_name}",
                    ask_vars=True,
                )
                draft.awx_template_id = tmpl.get("id")
                result["awx_template_id"] = draft.awx_template_id
        except Exception as exc:
            log.warning("publish_playbook: AWX failed: %s", exc)
            result["awx_error"] = str(exc)

    draft.status = "published"
    await db.commit()
    result["status"] = "published"
    return result
