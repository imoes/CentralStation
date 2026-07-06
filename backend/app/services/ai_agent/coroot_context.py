"""Coroot service-layer context for the AI situation analysis.

Feeds the KI-Lagebild the observability signals CheckMK CANNOT provide:
  - application-layer (APM) health: latency, error rate, restarts, log errors
  - eBPF service dependencies → application-layer blast radius
  - Coroot risks (unreplicated DB, single instance, OOM risk, …)

Deliberately EXCLUDES node CPU/RAM/disk/network — those are host-level metrics
already covered by CheckMK (RRD) and would be redundant. See get_application_health()
in the connector, which drops those signals at the source.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

log = logging.getLogger(__name__)

# Keep the LLM context bounded — the situation prompt already carries alerts,
# blast-radius and RAG context; Coroot adds a focused application-layer section.
_MAX_DEGRADED_APPS = 12
_MAX_RISK_TYPES = 8
_MAX_DEP_APPS = 8


async def _load_connectors(db: Any) -> list:
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.coroot import CorootConnector

    res = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "coroot",
            ConnectorConfig.enabled.is_(True),
        )
    )
    out = []
    for cfg in res.scalars().all():
        try:
            creds = decrypt_credentials(cfg.encrypted_credentials)
            out.append(CorootConnector(base_url=cfg.base_url, credentials=creds))
        except Exception as exc:  # noqa: BLE001
            log.warning("coroot_context: connector %s load failed: %s", cfg.name, exc)
    return out


def _risk_label(risk: dict) -> str:
    key = risk.get("key") or {}
    rtype = (key.get("type") or "").replace("-", " ")
    cat = key.get("category") or ""
    return f"{rtype} ({cat})" if cat else rtype


async def build_coroot_context(db: Any, alert_hosts: set[str] | None = None) -> str:
    """Return a compact, LLM-ready Coroot section (empty string if nothing to add).

    alert_hosts: lowercased host/app names already present in the alert set — used
    to surface the application-layer blast radius for exactly those services.
    """
    connectors = await _load_connectors(db)
    if not connectors:
        return ""

    alert_hosts = alert_hosts or set()
    health: list[dict] = []
    risks: list[dict] = []
    service_map: list[dict] = []
    for svc in connectors:
        try:
            health.extend(await svc.get_application_health())
        except Exception as exc:  # noqa: BLE001
            log.warning("coroot_context: application_health failed: %s", exc)
        try:
            risks.extend(await svc.get_risks())
        except Exception as exc:  # noqa: BLE001
            log.warning("coroot_context: risks failed: %s", exc)
        try:
            service_map.extend(await svc.get_service_map())
        except Exception as exc:  # noqa: BLE001
            log.warning("coroot_context: service_map failed: %s", exc)

    parts: list[str] = []

    # ── Degraded applications (APM) ────────────────────────────────────────────
    if health:
        crit = [a for a in health if a.get("status") == "critical"]
        warn = [a for a in health if a.get("status") not in ("critical",)]
        ordered = (crit + warn)[:_MAX_DEGRADED_APPS]
        lines = []
        for a in ordered:
            sig = ", ".join(f"{k}: {v}" for k, v in (a.get("signals") or {}).items())
            typ = f" [{a['type']}]" if a.get("type") else ""
            lines.append(f"- {a['app']}{typ} — {a.get('status','')}: {sig or 'degradiert'}")
        more = len(health) - len(ordered)
        suffix = f"\n  (+{more} weitere degradierte Anwendungen)" if more > 0 else ""
        parts.append(
            "Coroot APM — degradierte Anwendungen (Applikationsebene, NICHT in CheckMK sichtbar):\n"
            + "\n".join(lines) + suffix
        )

    # ── Application-layer blast radius for alerting hosts ───────────────────────
    if alert_hosts and service_map:
        by_app = {a["app"].lower(): a for a in service_map}
        dep_lines = []
        for host in sorted(alert_hosts):
            node = by_app.get(host)
            if not node:
                continue
            downs = [d["to"] for d in node.get("downstreams", [])][:6]
            ups = [u["to"] for u in node.get("upstreams", [])][:6]
            if not downs and not ups:
                continue
            detail = []
            if downs:
                detail.append(f"nutzen diesen Dienst: {', '.join(downs)}")
            if ups:
                detail.append(f"hängt ab von: {', '.join(ups)}")
            dep_lines.append(f"- {node['app']}: " + " | ".join(detail))
            if len(dep_lines) >= _MAX_DEP_APPS:
                break
        if dep_lines:
            parts.append(
                "Coroot Service-Abhängigkeiten der betroffenen Dienste (eBPF-beobachtet):\n"
                + "\n".join(dep_lines)
            )

    # ── Risks (grouped) ─────────────────────────────────────────────────────────
    if risks:
        grouped: Counter = Counter(_risk_label(r) for r in risks)
        lines = []
        for label, count in grouped.most_common(_MAX_RISK_TYPES):
            examples = [
                (r.get("application_id", "").split(":")[-1])
                for r in risks if _risk_label(r) == label
            ]
            examples = [e for e in examples if e][:4]
            ex = f" — z.B. {', '.join(examples)}" if examples else ""
            lines.append(f"- {count}× {label}{ex}")
        parts.append(
            "Coroot Risiken (proaktive Inspektionen, kein CheckMK-Äquivalent):\n"
            + "\n".join(lines)
        )

    if not parts:
        return ""
    return "\n\nCoroot-Observability (Applikationsebene):\n" + "\n\n".join(parts)
