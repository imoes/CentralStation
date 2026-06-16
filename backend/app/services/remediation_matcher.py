"""LLM-based AWX template matcher — proposes a remediation for a finding."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


async def propose_remediation(
    finding_title: str,
    rationale: str,
    host: str,
    external_id: str | None,
    analysis_id: uuid.UUID | None,
    db: Any,
) -> "RemediationProposal | None":
    """Match a finding against available AWX job templates and persist a proposal.

    Returns the new `RemediationProposal` or None when no suitable template
    is found or the AWX connector is not configured.
    """
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.awx import AWXConnector
    from app.models.remediation import RemediationProposal
    from app.services.settings import get_llm_config
    from app.services.llm_client import generate_text

    # ── 1. Load AWX connector ─────────────────────────────────────
    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "awx",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        log.debug("remediation_matcher: no AWX connector, skipping")
        return None

    creds = decrypt_credentials(cfg.encrypted_credentials)
    awx = AWXConnector(base_url=cfg.base_url, credentials=creds)

    # ── 2. Fetch template catalog ─────────────────────────────────
    try:
        templates = await awx.list_job_templates()
    except Exception as exc:
        log.warning("remediation_matcher: list_job_templates failed: %s", exc)
        return None

    if not templates:
        return None

    catalog = [
        {"id": t["id"], "name": t["name"], "description": t.get("description", "")}
        for t in templates
    ]

    # ── 3a. Deterministischer Label-Vorfilter ─────────────────────
    # cs-meta.matches-Patterns werden als AWX-Labels gespeichert.
    # Wenn ein Template-Label exakt im finding_title oder rationale vorkommt,
    # nehmen wir dieses Template direkt — kein LLM nötig.
    title_lower    = finding_title.lower()
    rationale_lower = rationale.lower()

    for t in templates:
        labels: list[dict] = t.get("related", {}).get("labels", {}).get("results", [])
        if not labels:
            # Fallback: parse matches from description field ("Matches: pattern1, pattern2")
            desc = t.get("description", "")
            if "Matches:" in desc:
                match_line = desc.split("Matches:", 1)[1].split("\n")[0]
                labels = [{"name": p.strip()} for p in match_line.split(",") if p.strip()]

        for label in labels:
            pattern = label.get("name", "").lower()
            if not pattern:
                continue
            # Wildcard suffix: "checkmk:filesystem*" → prefix match
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                if prefix in title_lower or prefix in rationale_lower:
                    log.info(
                        "remediation_matcher: label-prefilter hit '%s' → template '%s' (id=%s)",
                        label.get("name"), t["name"], t["id"],
                    )
                    template_id = t["id"]
                    template_name = t["name"]
                    # Skip LLM, persist directly
                    from app.models.remediation import RemediationProposal
                    proposal = RemediationProposal(
                        external_id=external_id,
                        host=host,
                        finding_title=finding_title,
                        rationale=f"{rationale}\n\nLabel-match: {label.get('name')}",
                        awx_template_id=template_id,
                        awx_template_name=template_name,
                        extra_vars={},
                        risk="medium",
                        status="proposed",
                        analysis_id=analysis_id,
                    )
                    db.add(proposal)
                    await db.flush()
                    return proposal
            else:
                if pattern in title_lower or pattern in rationale_lower:
                    log.info(
                        "remediation_matcher: label-prefilter hit '%s' → template '%s' (id=%s)",
                        label.get("name"), t["name"], t["id"],
                    )
                    template_id = t["id"]
                    from app.models.remediation import RemediationProposal
                    proposal = RemediationProposal(
                        external_id=external_id,
                        host=host,
                        finding_title=finding_title,
                        rationale=f"{rationale}\n\nLabel-match: {label.get('name')}",
                        awx_template_id=template_id,
                        awx_template_name=t["name"],
                        extra_vars={},
                        risk="medium",
                        status="proposed",
                        analysis_id=analysis_id,
                    )
                    db.add(proposal)
                    await db.flush()
                    return proposal

    # ── 3b. LLM picks the best match (Fallback) ───────────────────
    llm_cfg = await get_llm_config(db)
    if not llm_cfg.is_configured:
        return None

    catalog_json = json.dumps(catalog, ensure_ascii=False, indent=None)
    prompt = (
        f"You are an automation engineer. A monitoring system detected the following problem:\n"
        f"Host: {host}\nFinding: {finding_title}\nDetail: {rationale}\n\n"
        f"Available AWX job templates (JSON array):\n{catalog_json}\n\n"
        f"Select the single best matching template to remediate this problem, or respond with "
        f'null if none is suitable. Respond ONLY with JSON in one of these forms:\n'
        f'{{"template_id": <int>, "extra_vars": {{}}, "risk": "low|medium|high", "reason": "<why>"}}\n'
        f"or null"
    )

    try:
        raw = await generate_text(
            llm_cfg,
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_output_tokens=300,
        )
        raw = raw.strip()
        if raw.lower() in ("null", "none", ""):
            log.debug("remediation_matcher: LLM found no matching template")
            return None
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        decision = json.loads(raw)
    except Exception as exc:
        log.warning("remediation_matcher: LLM parse failed: %s | raw=%r", exc, raw if "raw" in dir() else "")
        return None

    template_id = decision.get("template_id")
    if not template_id:
        return None

    tmpl = next((t for t in templates if t["id"] == template_id), None)
    template_name = tmpl["name"] if tmpl else str(template_id)

    # ── 4. Persist proposal ───────────────────────────────────────
    proposal = RemediationProposal(
        external_id=external_id,
        host=host,
        finding_title=finding_title,
        rationale=f"{rationale}\n\nLLM reason: {decision.get('reason', '')}",
        awx_template_id=template_id,
        awx_template_name=template_name,
        extra_vars=decision.get("extra_vars") or {},
        risk=decision.get("risk", "medium"),
        status="proposed",
        analysis_id=analysis_id,
    )
    db.add(proposal)
    await db.flush()
    log.info(
        "remediation_matcher: proposed template '%s' (id=%s) for '%s' @ %s",
        template_name, template_id, finding_title[:60], host,
    )
    return proposal
