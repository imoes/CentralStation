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
    # Feature toggle (shared with the topology layer): coroot.enrichment_enabled.
    try:
        from app.services.settings import get_all_settings
        _s = await get_all_settings(db)
        if (_s.get("coroot.enrichment_enabled") or "true").lower() == "false":
            return ""
    except Exception:  # noqa: BLE001
        pass

    connectors = await _load_connectors(db)
    if not connectors:
        return ""

    alert_hosts = alert_hosts or set()
    health: list[dict] = []
    risks: list[dict] = []
    service_map: list[dict] = []
    placements: dict[str, list[str]] = {}
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

    # ── Degraded applications (APM) + their host placement ──────────────────────
    # Coroot apps are containers; the "runs on host X" link (X = NetBox host) is the
    # bridge that lets the LLM connect a CheckMK host alert → containers on it →
    # dependent services. Placement is fetched only for the shown apps (bounded).
    if health:
        crit = [a for a in health if a.get("status") == "critical"]
        warn = [a for a in health if a.get("status") not in ("critical",)]
        ordered = (crit + warn)[:_MAX_DEGRADED_APPS]

        try:
            shown_names = [a["app"] for a in ordered]
            for svc in connectors:
                placements.update(await svc.get_app_placements(shown_names))
        except Exception as exc:  # noqa: BLE001
            log.warning("coroot_context: placements failed: %s", exc)

        lines = []
        for a in ordered:
            sig = ", ".join(f"{k}: {v}" for k, v in (a.get("signals") or {}).items())
            typ = f" [{a['type']}]" if a.get("type") else ""
            hosts = placements.get(a["app"].lower()) or []
            on_host = f" — läuft auf {', '.join(hosts)}" if hosts else ""
            lines.append(f"- {a['app']}{typ}{on_host} — {a.get('status','')}: {sig or 'degradiert'}")
        more = len(health) - len(ordered)
        suffix = f"\n  (+{more} weitere degradierte Anwendungen)" if more > 0 else ""
        parts.append(
            "Coroot APM — degradierte Container/Anwendungen mit Host-Zuordnung "
            "(Applikationsebene, NICHT in CheckMK sichtbar; 'läuft auf' = NetBox-Host):\n"
            + "\n".join(lines) + suffix
        )

    # ── Cross-layer blast radius: alerting HOST → containers on it → dependents ─
    # This is the key signal: a CheckMK host alert (NetBox host) is linked to the
    # Coroot containers running there and, via the eBPF map, to the services that
    # depend on those containers. Reuses the placements already fetched above.
    if alert_hosts and service_map and placements:
        by_app = {a["app"].lower(): a for a in service_map}
        # reverse: NetBox host → [container apps] (from the degraded-app placements)
        host_to_apps: dict[str, list[str]] = {}
        for app_short, hosts in (placements or {}).items():
            for h in hosts:
                host_to_apps.setdefault(h.lower(), []).append(app_short)
        dep_lines = []
        for host in sorted(alert_hosts):
            containers = host_to_apps.get(host)
            if not containers:
                continue
            for cont in containers:
                node = by_app.get(cont)
                downs = [d["to"] for d in (node.get("downstreams", []) if node else [])][:6]
                detail = f"Container '{cont}'"
                if downs:
                    detail += f" — davon abhängige Dienste: {', '.join(downs)}"
                dep_lines.append(f"- Host {host}: {detail}")
                if len(dep_lines) >= _MAX_DEP_APPS:
                    break
            if len(dep_lines) >= _MAX_DEP_APPS:
                break
        if dep_lines:
            parts.append(
                "Coroot Cross-Layer-Blast-Radius (Host-Alert → Container darauf → abhängige Dienste, "
                "eBPF-beobachtet — diese Kausalkette ist in CheckMK nicht sichtbar):\n"
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
