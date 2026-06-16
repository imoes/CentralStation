"""KI-basierter Playbook-Author: generiert Ansible-YAML mit cs-meta-Block und publiziert in AWX."""
from __future__ import annotations

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
    """LLM generates an Ansible playbook YAML with cs-meta block.

    Returns a `PlaybookDraft` in status 'drafted', or None on failure.
    """
    from app.models.remediation import PlaybookDraft
    from app.services.settings import get_llm_config
    from app.services.llm_client import generate_text
    from app.services.playbook_meta import parse_meta

    llm_cfg = await get_llm_config(db)
    if not llm_cfg.is_configured:
        return None

    prompt = (
        "You are an Ansible automation engineer for a Linux infrastructure team. "
        "Write a complete, valid Ansible playbook YAML for the following task.\n\n"
        "Rules:\n"
        "1. Start with a cs-meta block (commented YAML, see format below).\n"
        "2. Use ansible.builtin.* FQCNs for all standard modules.\n"
        "3. Use vars for any host-specific parameters so the playbook can be launched "
        "via AWX with extra_vars. Always include a '{{ target_host }}' variable for hosts:.\n"
        "4. Keep tasks idempotent where possible.\n\n"
        "cs-meta block format (MUST be first, before the YAML document start ---):\n"
        "# ─── cs-meta ───────────────────────────────────────────────\n"
        "# id: <kebab-case-id>\n"
        "# title: <Human readable title>\n"
        "# description: <One line description>\n"
        '# matches: ["<checkmk alert pattern or keyword>", ...]\n'
        "# target: linux  # linux | windows | network | generic\n"
        "# risk: low  # low | medium | high\n"
        "# params:\n"
        '#   - {name: param_name, description: "what it does", example: "value"}\n'
        "# ─── /cs-meta ──────────────────────────────────────────────\n\n"
        f"Task: {task_description}\n"
        f"Context: {context}\n\n"
        "Respond ONLY with the raw YAML including the cs-meta block. No markdown fences, no explanation."
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

    yaml_content = raw.strip()

    # Parse cs-meta from the generated content
    meta = parse_meta(yaml_content) or {}

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
    """Commit playbook to the Manual AWX project directory, create AWX job template.

    For the Manual SCM project the playbook file is written directly to the local
    filesystem (./playbooks/ bind-mounted into AWX at /var/lib/awx/projects/local/).
    No Git sync needed — AWX sees changes immediately.

    Returns a dict with status information.
    """
    import os
    from sqlalchemy import select
    from app.models.remediation import PlaybookDraft
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.awx import AWXConnector
    from app.services.playbook_meta import parse_meta, validate_meta, meta_to_awx_description, meta_to_survey_spec

    draft = (await db.execute(select(PlaybookDraft).where(PlaybookDraft.id == draft_id))).scalar_one_or_none()
    if not draft:
        return {"error": "Draft not found"}
    if draft.status != "drafted":
        return {"error": f"Draft is already '{draft.status}'"}

    result: dict = {}

    # ── Parse cs-meta from the playbook ───────────────────────
    meta = parse_meta(draft.yaml) or {}
    errors = validate_meta(meta)
    if errors:
        log.warning("publish_playbook: cs-meta invalid for '%s': %s", draft.title, errors)
        result["meta_warnings"] = errors

    safe_name = (meta.get("id") or draft.title.lower().replace(" ", "_")[:50]) + ".yml"

    # ── 1. Write to local playbooks dir (Manual AWX project) ──
    playbooks_path = os.getenv("IDE_PLAYBOOKS_PATH", "/opt/centralstation/playbooks")
    if os.path.isdir(playbooks_path):
        try:
            dest = os.path.join(playbooks_path, safe_name)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(draft.yaml)
            result["file"] = dest
            log.info("publish_playbook: wrote %s", dest)
        except OSError as exc:
            log.warning("publish_playbook: file write failed: %s", exc)
            result["file_error"] = str(exc)
    else:
        result["file_warning"] = f"IDE_PLAYBOOKS_PATH '{playbooks_path}' not found, skipped file write"

    # ── 2. AWX job template ───────────────────────────────────
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

            description = meta_to_awx_description(meta) if meta else draft.description or ""
            survey_spec = meta_to_survey_spec(meta) if meta else None
            matches = meta.get("matches", [])

            tmpl = await awx.create_job_template(
                name=f"[CS] {draft.title}",
                playbook=safe_name,
                description=description,
                matches=matches,
                survey_spec=survey_spec,
                ask_vars=True,
            )
            draft.awx_template_id = tmpl.get("id")
            result["awx_template_id"] = draft.awx_template_id
            log.info("publish_playbook: AWX template '%s' id=%s", draft.title, draft.awx_template_id)
        except Exception as exc:
            log.warning("publish_playbook: AWX failed: %s", exc)
            result["awx_error"] = str(exc)

    draft.status = "published"
    await db.commit()
    result["status"] = "published"
    return result
