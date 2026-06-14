"""Lern-Loop: schreibt Remediation-Ergebnisse zurück in OpenSearch, AlertComments und AIKB."""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any

log = logging.getLogger(__name__)


async def record_remediation_outcome(
    proposal_id: _uuid.UUID,
    db: Any,
) -> None:
    """Called after a RemediationProposal reaches a terminal status.

    On success:
    - Updates OpenSearch ai_resolution_text / ai_insight for the linked alert.
    - Writes an AlertComment(kind="ai") for the CentralStation feed.
    - Optionally creates a [KB] runbook in IT-AIKB.

    On failure: annotates with a warning note so the next run can avoid the same template.
    """
    from sqlalchemy import select
    from app.models.remediation import RemediationProposal
    from app.services.feed_index import update_ai_resolution

    proposal = (
        await db.execute(select(RemediationProposal).where(RemediationProposal.id == proposal_id))
    ).scalar_one_or_none()
    if not proposal:
        log.warning("remediation_learning: proposal %s not found", proposal_id)
        return

    succeeded = proposal.status == "succeeded"
    summary = _build_summary(proposal, succeeded)

    # ── 1. OpenSearch annotation ──────────────────────────────────
    if proposal.external_id:
        try:
            await update_ai_resolution(proposal.external_id, summary)
        except Exception as exc:
            log.warning("remediation_learning: OS update failed: %s", exc)

    # ── 2. AlertComment ───────────────────────────────────────────
    if proposal.external_id:
        try:
            await _write_alert_comment(proposal.external_id, summary, db)
        except Exception as exc:
            log.warning("remediation_learning: AlertComment failed: %s", exc)

    # ── 3. [KB] Runbook bei Erfolg ────────────────────────────────
    if succeeded:
        try:
            await _create_kb_runbook(proposal, summary, db)
        except Exception as exc:
            log.warning("remediation_learning: KB runbook failed: %s", exc)

    log.info(
        "remediation_learning: recorded outcome for proposal %s (status=%s)",
        str(proposal_id)[:8],
        proposal.status,
    )


def _build_summary(proposal: Any, succeeded: bool) -> str:
    outcome = "erfolgreich behoben" if succeeded else f"Fehler ({proposal.status})"
    lines = [
        f"Automated Remediation — {outcome}",
        f"Finding: {proposal.finding_title}",
    ]
    if proposal.host:
        lines.append(f"Host: {proposal.host}")
    if proposal.awx_template_name:
        lines.append(f"AWX-Template: {proposal.awx_template_name}")
    if proposal.extra_vars:
        lines.append(f"Extra-Vars: {proposal.extra_vars}")
    if proposal.rationale:
        lines.append(f"Begründung: {proposal.rationale}")
    if not succeeded and proposal.stdout:
        lines.append(f"Fehler-Auszug: {proposal.stdout[-500:]}")
    elif succeeded and proposal.stdout:
        lines.append("Playbook-Ausgabe vorhanden (siehe Maschinenraum).")
    return "\n".join(lines)


async def _write_alert_comment(external_id: str, text: str, db: Any) -> None:
    from app.models.workflow import AlertComment

    db.add(AlertComment(
        external_id=external_id,
        user_id=None,
        user_name="Maschinenraum (KI)",
        kind="ai",
        body=text[:2000],
    ))
    await db.commit()


async def _create_kb_runbook(proposal: Any, summary: str, db: Any) -> None:
    """Tries to create a [KB] article in IT-AIKB via the connector."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials

    cfg = (await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "aikb",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )).scalar_one_or_none()
    if not cfg:
        return

    creds = decrypt_credentials(cfg.encrypted_credentials)
    base_url = cfg.base_url
    token = creds.get("token") or creds.get("api_key") or creds.get("password", "")

    title = f"[KB] Remediation: {proposal.finding_title}"
    body = (
        f"# {title}\n\n"
        f"**Host:** {proposal.host or 'n/a'}  \n"
        f"**AWX-Template:** {proposal.awx_template_name or 'n/a'}  \n"
        f"**Extra-Vars:** `{proposal.extra_vars or {}}`\n\n"
        "## Symptom\n\n"
        f"{proposal.rationale or proposal.finding_title}\n\n"
        "## Durchgeführte Maßnahme\n\n"
        f"AWX-Job `{proposal.awx_job_id}` mit Template **{proposal.awx_template_name}** "
        f"wurde ausgeführt und hat das Problem **erfolgreich behoben**.\n\n"
        "## Wiederverwendung\n\n"
        "Bei erneutem Auftreten desselben Findings kann dieses Template direkt gestartet werden."
    )

    import httpx
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(verify=False, timeout=15) as c:
        await c.post(
            f"{base_url}/api/articles",
            json={"title": title, "body": body, "category": "runbooks", "tags": ["remediation", "automated"]},
            headers=headers,
        )
