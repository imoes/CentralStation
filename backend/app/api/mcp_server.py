"""CentralStation FastMCP Server — exposes IT-Ops tools for Hermes.

Hermes configures this as an MCP server and can call these tools to query
alerts, check hosts, search logs, acknowledge alerts, and create tickets.

Mounted at /api/mcp in main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastmcp import FastMCP

log = logging.getLogger(__name__)

mcp = FastMCP("CentralStation IT-Ops", version="1.0.0")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_os():
    from app.core.opensearch import get_opensearch
    return get_opensearch()  # sync singleton, no await


# ── Tool 1: Bridge Status ──────────────────────────────────────────

@mcp.tool()
async def get_bridge_status() -> dict:
    """Gibt den aktuellen IT-Systemstatus zurück: Alert-Schweregrade, offene Incidents,
    Fleet-Vitals (Disk/RAM/CPU) und aktive Probleme pro Quelle (CheckMK, Graylog, Wazuh).
    Nutze dieses Tool für allgemeine Statusabfragen wie 'Wie ist der aktuelle Stand?'"""
    from sqlalchemy import select, func
    from app.models.alert import Alert

    async with (await _get_db_session()) as db:
        sources = ["checkmk", "graylog", "wazuh"]
        severities = ["critical", "high", "medium", "low", "info"]

        # Count alerts by source + severity (last 6h)
        since = _now_utc() - timedelta(hours=6)
        result = await db.execute(
            select(Alert.source, Alert.severity, func.count())
            .where(Alert.status != "resolved", Alert.created_at >= since)
            .group_by(Alert.source, Alert.severity)
        )
        rows = result.all()

        src_counts: dict[str, dict] = {s: {sev: 0 for sev in severities} for s in sources}
        for source, severity, cnt in rows:
            if source in src_counts and severity in src_counts[source]:
                src_counts[source][severity] += cnt

        total_crit = sum(src_counts[s]["critical"] for s in sources)
        total_high = sum(src_counts[s]["high"] for s in sources)
        total_med  = sum(src_counts[s]["medium"] for s in sources)
        total      = sum(sum(src_counts[s].values()) for s in sources)

        state = "red" if total_crit > 0 else ("yellow" if total_high > 0 else "green")

        return {
            "alert_state": state,
            "counts": {"critical": total_crit, "high": total_high, "medium": total_med, "total": total},
            "sources": [
                {
                    "name": s,
                    "critical": src_counts[s]["critical"],
                    "high": src_counts[s]["high"],
                    "total": sum(src_counts[s].values()),
                }
                for s in sources
            ],
            "timestamp": _now_utc().isoformat(),
        }


# ── Tool 2: List Alerts ────────────────────────────────────────────

@mcp.tool()
async def list_alerts(
    hours: int = 6,
    severity: str = "",
    source: str = "",
    limit: int = 20,
) -> list[dict]:
    """Listet aktive Alerts aus CheckMK, Graylog und Wazuh.

    Parameter:
    - hours: Zeitraum in Stunden (1-72, Standard 6)
    - severity: Filter nach Schweregrad (critical/high/medium/low/info, leer = alle)
    - source: Filter nach Quelle (checkmk/graylog/wazuh, leer = alle)
    - limit: Maximale Anzahl Ergebnisse (max 50)

    Nutze dieses Tool wenn der Nutzer nach aktuellen Problemen oder Alerts fragt."""
    from sqlalchemy import select
    from app.models.alert import Alert

    hours = max(1, min(72, hours))
    limit = max(1, min(50, limit))
    since = _now_utc() - timedelta(hours=hours)

    stmt = select(Alert).where(Alert.status != "resolved", Alert.created_at >= since)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if source:
        stmt = stmt.where(Alert.source == source)
    stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)

    async with (await _get_db_session()) as db:
        rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": str(a.id),
            "external_id": a.external_id or "",
            "source": a.source,
            "severity": a.severity,
            "title": a.title,
            "host": a.metadata_.get("host", "") if a.metadata_ else "",
            "location": a.location_name or "",
            "status": a.status,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]


# ── Tool 3: Search Feed ────────────────────────────────────────────

@mcp.tool()
async def search_feed(query: str, index: str = "cs-feed-*", limit: int = 10) -> list[dict]:
    """Durchsucht den OpenSearch-Alert-Feed mit Lucene-Syntax.

    Parameter:
    - query: Lucene-Abfrage (z.B. 'severity:critical AND host:docker*')
    - index: OpenSearch-Index (Standard: cs-feed-*, oder cs-feed-checkmk, cs-feed-graylog, cs-feed-wazuh)
    - limit: Maximale Ergebnisse (max 30)

    Beispiele:
    - 'host:docker086'
    - 'severity:critical AND source:checkmk'
    - 'body:*disk*'

    Nutze dieses Tool für spezifische Log-Suchen."""
    limit = max(1, min(30, limit))
    os_client = _get_os()
    try:
        resp = await os_client.search(
            index=index,
            body={
                "query": {"query_string": {"query": query, "default_field": "body"}},
                "size": limit,
                "sort": [{"created_at": {"order": "desc"}}],
            },
            ignore_unavailable=True,
        )
        hits = resp.get("hits", {}).get("hits", [])
        return [
            {
                "id": h.get("_id", ""),
                "external_id": h["_source"].get("external_id", ""),
                "source": h["_source"].get("source", ""),
                "severity": h["_source"].get("severity", ""),
                "title": h["_source"].get("title", ""),
                "body": (h["_source"].get("body", "") or "")[:300],
                "host": h["_source"].get("host", ""),
                "ai_insight": h["_source"].get("ai_insight", ""),
                "created_at": h["_source"].get("created_at", ""),
            }
            for h in hits
        ]
    except Exception as exc:
        log.warning("search_feed error: %s", exc)
        return []


# ── Tool 4: Acknowledge Alert ──────────────────────────────────────

@mcp.tool()
async def acknowledge_alert(alert_id: str) -> dict:
    """Bestätigt einen Alert (setzt Status auf 'acknowledged').

    Parameter:
    - alert_id: UUID des Alerts (aus list_alerts)

    Nutze dieses Tool wenn der Nutzer einen Alert quittieren/bestätigen möchte."""
    import uuid as uuid_mod
    from sqlalchemy import select
    from app.models.alert import Alert

    try:
        uid = uuid_mod.UUID(alert_id)
    except ValueError:
        return {"ok": False, "error": "Ungültige Alert-ID"}

    async with (await _get_db_session()) as db:
        row = (await db.execute(select(Alert).where(Alert.id == uid))).scalar_one_or_none()
        if not row:
            return {"ok": False, "error": "Alert nicht gefunden"}
        row.status = "acknowledged"
        await db.commit()
    return {"ok": True, "alert_id": alert_id, "new_status": "acknowledged"}


# ── Tool 5: Get CheckMK Host ───────────────────────────────────────

def _checkmk_trend(series: list[dict]) -> tuple[float | None, float | None, float | None, str]:
    """Return (current, min, max, trend_arrow) from an RRD time series."""
    vals = [p["value"] for p in series if p.get("value") is not None]
    if not vals:
        return None, None, None, "?"
    current = vals[-1]
    mn, mx = min(vals), max(vals)
    mid = len(vals) // 2 or 1
    avg_first = sum(vals[:mid]) / mid
    avg_last = sum(vals[mid:]) / max(len(vals[mid:]), 1)
    if avg_last > avg_first * 1.07:
        arrow = "↑"
    elif avg_last < avg_first * 0.93:
        arrow = "↓"
    else:
        arrow = "→"
    return current, mn, mx, arrow


async def _checkmk_configs():
    """Return all enabled CheckMK connector configs + credentials."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    async with (await _get_db_session()) as db:
        cfgs = (await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "checkmk",
                ConnectorConfig.enabled.is_(True),
            )
        )).scalars().all()
    return [(cfg, decrypt_credentials(cfg.encrypted_credentials)) for cfg in cfgs]


async def _fetch_host_performance(hostname: str, hours: int = 2) -> dict:
    """Fetch fresh RRD metrics for a host from all enabled CheckMK sites.

    Tries every site until the host is found. Returns {site, metrics:[]} or {error}.
    Each metric entry: {service, metric, current, min, max, trend, unit}.
    """
    from app.services.connectors.checkmk import CheckMKConnector
    from app.services.metrics_collector import _DEFAULT_METRICS

    configs = await _checkmk_configs()
    if not configs:
        return {"error": "Kein CheckMK-Connector konfiguriert"}

    for cfg, creds in configs:
        connector = CheckMKConnector(base_url=cfg.base_url, credentials=creds)
        # Verify host exists on this site
        try:
            services = await connector.list_services(hostname)
        except Exception:
            services = []
        if not services:
            continue

        metrics_out: list[dict] = []
        for m in _DEFAULT_METRICS:
            data = await connector.get_graph_data(
                hostname, m["service"], metric_id=m["metric_id"], hours=hours
            )
            series = data.get("series", [])
            if not series:
                continue
            cur, mn, mx, arrow = _checkmk_trend(series)
            unit = m.get("unit", "")
            # Convert raw bytes to GB for readability
            if unit == "bytes" and cur is not None:
                cur, mn, mx = cur / 1e9, mn / 1e9, mx / 1e9
                unit = "GB"
            metrics_out.append({
                "service": m["service"],
                "metric":  m["metric_id"],
                "current": round(cur, 2) if cur is not None else None,
                "min":     round(mn,  2) if mn  is not None else None,
                "max":     round(mx,  2) if mx  is not None else None,
                "trend":   arrow,
                "unit":    unit,
            })
        return {"hostname": hostname, "site": cfg.name, "hours": hours, "metrics": metrics_out}

    return {"hostname": hostname, "error": "Host auf keinem CheckMK-Standort gefunden"}


@mcp.tool()
async def get_checkmk_host(hostname: str) -> dict:
    """Ruft den CheckMK-Status eines Hosts ab: alle Services + aktuelle Performance-Metriken (CPU, RAM, Disk).

    Parameter:
    - hostname: Hostname oder FQDN (z.B. 'docker086' oder 'docker086.ippen.media')

    Nutze dieses Tool wenn der Nutzer den Status eines bestimmten Servers wissen will."""
    from app.services.connectors.checkmk import CheckMKConnector

    configs = await _checkmk_configs()
    if not configs:
        return {"error": "Kein CheckMK-Connector konfiguriert"}

    errors: list[str] = []
    for cfg, creds in configs:
        connector = CheckMKConnector(base_url=cfg.base_url, credentials=creds)
        try:
            services = await connector.list_services(hostname)
            if not services:
                continue
            # Fetch fresh performance metrics in parallel with the service query
            perf = await _fetch_host_performance(hostname, hours=2)
            return {
                "hostname": hostname,
                "site": cfg.name,
                "services": services,
                "performance": perf.get("metrics", []),
            }
        except Exception as exc:
            log.warning("get_checkmk_host %s on '%s': %s", hostname, cfg.name, exc)
            errors.append(f"{cfg.name}: {exc}")

    return {
        "hostname": hostname,
        "error": "Host auf keinem CheckMK-Standort gefunden",
        "details": errors or None,
    }


# ── Tool: Get CheckMK Performance Metrics (RRD) ───────────────────────────────

@mcp.tool()
async def get_checkmk_performance(hostname: str, hours: int = 2) -> dict:
    """Ruft frische RRD-Performance-Metriken direkt aus CheckMK ab (CPU, RAM, Disk).

    Immer live von CheckMK — kein Cache. Liefert aktuellen Wert, Min/Max im Zeitfenster
    und Trend-Richtung (↑ steigend / → stabil / ↓ fallend).

    Parameter:
    - hostname: Hostname (z.B. 'docker086' oder 'docker086.ippen.media')
    - hours:    Zeitfenster in Stunden für Trend-Berechnung (Standard: 2)

    Nutze dieses Tool um Performance-Entwicklungen, Lastmuster und Kapazitäts-
    engpässe zu erkennen — z.B. beim Analysieren von Alerts oder bei der Suche
    nach Anomalien."""
    return await _fetch_host_performance(hostname, hours=hours)


# ── Tools: CheckMK Hostgroup Pattern Analysis ─────────────────────────────────

async def _first_checkmk_connector():
    """Return a CheckMKConnector for the first enabled site, or None."""
    from app.services.connectors.checkmk import CheckMKConnector
    configs = await _checkmk_configs()
    if not configs:
        return None
    cfg, creds = configs[0]
    return CheckMKConnector(base_url=cfg.base_url, credentials=creds)


@mcp.tool()
async def list_checkmk_hostgroups(search: str = "") -> dict:
    """Listet CheckMK-Hostgruppen (optional gefiltert per Namens-Substring).

    Parameter:
    - search: optionaler Teilstring zum Filtern der Gruppennamen (z.B. 'cue')

    Nutze dieses Tool um verfügbare Hostgruppen für eine Performance-Musteranalyse
    zu finden (z.B. vor get_hostgroup_performance_summary / analyze_hostgroup_patterns)."""
    conn = await _first_checkmk_connector()
    if not conn:
        return {"error": "Kein CheckMK-Connector konfiguriert"}
    try:
        r = await conn._request("GET", "/domain-types/host_group_config/collections/all")
        r.raise_for_status()
        groups = [g.get("id", "") for g in r.json().get("value", []) if g.get("id")]
    except Exception as exc:
        return {"error": f"CheckMK-Abfrage fehlgeschlagen: {exc}"}
    if search:
        groups = [g for g in groups if search.lower() in g.lower()]
    return {"groups": sorted(groups), "count": len(groups)}


@mcp.tool()
async def get_hostgroup_performance_summary(group_name: str, top_n: int = 15) -> dict:
    """Frische, kompakte Performance-Zusammenfassung einer CheckMK-Hostgruppe.

    Vergleicht Performance-Metriken (CPU, RAM, Disk, HTTP-Antwortzeiten, 5xx-Rate)
    über mehrere Zeitfenster, erkennt Anomalien und Korrelationen — reine Daten,
    KEINE LLM. Liefert Fleet-Aggregate, akute Abweichungen, Cross-Metrik-
    Korrelationen, Peak-Zeit-Cluster und eine Anomalie-Shortlist.

    Parameter:
    - group_name: CheckMK-Hostgruppe (z.B. 'cue-prod')
    - top_n: Anzahl der auffälligsten Host/Metrik-Einträge (Standard 15)

    Nutze dieses Tool um selbst über die Daten zu argumentieren. Für bereits
    benannte Muster siehe analyze_hostgroup_patterns."""
    from app.services import hostgroup_analysis as hga
    conn = await _first_checkmk_connector()
    if not conn:
        return {"error": "Kein CheckMK-Connector konfiguriert"}
    async with (await _get_db_session()) as db:
        bundle = await hga.analyze_hostgroup(
            conn, group_name, db=db, correlate_logs=False,
            windows=[hga.ACUTE_WINDOW, hga.CORR_WINDOW], top_n=top_n,
        )
    return bundle


@mcp.tool()
async def analyze_hostgroup_patterns(group_name: str, correlate_logs: bool = True) -> dict:
    """Erkennt und BENENNT Performance-/Fehlermuster über eine CheckMK-Hostgruppe.

    Korreliert Metriken untereinander (z.B. CPU-Load ↔ HTTP-Antwortzeit ↔ 5xx-Rate)
    und mit Graylog-Logs über vier Zeitfenster (4h/25h/8d/35d), und gibt benannte
    Muster mit Belegen zurück. Nutzt die Korrelations-/Anomalie-Analyse + ein LLM
    nur zur Benennung. Daten immer frisch aus CheckMK.

    Parameter:
    - group_name: CheckMK-Hostgruppe (z.B. 'cue-prod')
    - correlate_logs: Graylog-Logs der auffälligen Hosts einbeziehen (Standard True)

    Nutze dieses Tool für 'erkenne Muster in Hostgruppe X' / 'vergleiche die letzten
    Tage'. Das Ergebnis enthält benannte Muster + die zugrundeliegende Evidenz."""
    from app.services import hostgroup_analysis as hga
    from app.services.settings import get_active_llm_config

    conn = await _first_checkmk_connector()
    if not conn:
        return {"error": "Kein CheckMK-Connector konfiguriert"}

    async with (await _get_db_session()) as db:
        bundle = await hga.analyze_hostgroup(
            conn, group_name, db=db, correlate_logs=correlate_logs,
        )
        if bundle.get("error"):
            return bundle
        llm_config = await get_active_llm_config(db)

    named = await hga.name_patterns(bundle, llm_config)
    return {
        "group_name": group_name,
        "hosts": bundle.get("hosts"),
        "windows": bundle.get("windows"),
        "severity_summary": named.get("severity_summary", "none"),
        "patterns": named.get("patterns", []),
        "note": named.get("note"),
        "error": named.get("error"),
        "raw_shortlist": bundle.get("shortlist", []),
    }


# ── Tool 6: Create Jira Ticket ─────────────────────────────────────

@mcp.tool()
async def create_jira_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Erstellt ein Jira-Ticket im Standard-Projekt.

    Parameter:
    - title: Kurze Zusammenfassung des Problems (max 200 Zeichen)
    - description: Ausführliche Beschreibung
    - priority: Priorität (critical/high/medium/low, Standard: medium)

    Nutze dieses Tool wenn der Nutzer ein Ticket oder eine Aufgabe erstellen möchte."""
    import json as _json
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.services.connectors.jira import JiraConnector
    from app.models.settings import GlobalSetting
    from app.core.security import decrypt_credentials

    async with (await _get_db_session()) as db:
        cfg_result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type.in_(["jira", "jira_sd"]),
                ConnectorConfig.enabled.is_(True),
            ).limit(1)
        )
        cfg = cfg_result.scalar_one_or_none()
        if not cfg:
            return {"ok": False, "error": "Kein Jira-Connector konfiguriert"}

        # Default project from global settings
        proj_row = (await db.execute(
            select(GlobalSetting).where(GlobalSetting.key == "jira.default_project")
        )).scalar_one_or_none()
        project = proj_row.value_plain if proj_row else "IMIT"

        # Priority names are instance-specific. Default to the standard Jira
        # priority names (Highest/High/Medium/Low); override per instance via the
        # global setting jira.priority_map = {"critical": "Kritisch", ...}.
        prio_map = {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low"}
        map_row = (await db.execute(
            select(GlobalSetting).where(GlobalSetting.key == "jira.priority_map")
        )).scalar_one_or_none()
        if map_row and map_row.value_plain:
            try:
                prio_map.update(_json.loads(map_row.value_plain))
            except Exception:
                log.warning("jira.priority_map is not valid JSON, using defaults")
        jira_priority = prio_map.get(priority, prio_map.get("medium", ""))

        creds = decrypt_credentials(cfg.encrypted_credentials)
        connector = JiraConnector(base_url=cfg.base_url, credentials=creds)
        try:
            issue = await connector.create_issue(
                project=project,
                summary=title[:200],
                description=description,
                priority=jira_priority,
            )
            return {"ok": True, "jira_key": issue.get("key", ""), "url": issue.get("url", "")}
        except Exception as exc:
            # A wrong/unknown priority name rejects the whole create → retry once
            # without priority so the ticket still gets created.
            log.warning("create_jira_ticket failed (%s) — retrying without priority", exc)
            try:
                issue = await connector.create_issue(
                    project=project,
                    summary=title[:200],
                    description=description,
                    priority="",
                )
                return {"ok": True, "jira_key": issue.get("key", ""),
                        "url": issue.get("url", ""), "note": "ohne Priorität erstellt"}
            except Exception as exc2:
                log.warning("create_jira_ticket retry failed: %s", exc2)
                return {"ok": False, "error": str(exc2)}


# ── Tool 7: Get Alert Analysis ─────────────────────────────────────

@mcp.tool()
async def get_alert_analysis(external_id: str) -> dict:
    """Gibt gespeicherte KI-Analysen und Kommentare zu einem Alert zurück.

    Parameter:
    - external_id: Die externe ID des Alerts (aus list_alerts oder search_feed)

    Nutze dieses Tool wenn du wissen willst, was zu einem Alert bereits analysiert
    oder kommentiert wurde — z.B. für Incident-Untersuchungen."""
    from sqlalchemy import select
    from app.models.workflow import AlertComment

    async with (await _get_db_session()) as db:
        rows = (await db.execute(
            select(AlertComment)
            .where(AlertComment.external_id == external_id)
            .order_by(AlertComment.created_at.desc())
            .limit(20)
        )).scalars().all()

    return {
        "external_id": external_id,
        "comments": [
            {
                "kind": r.kind,
                "user_name": r.user_name,
                "body": r.body,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


# ── Tool 8: Post Alert Comment ─────────────────────────────────────

@mcp.tool()
async def post_alert_comment(external_id: str, text: str) -> dict:
    """Speichert eine Analyse oder einen Befund als Kommentar an einem Alert.

    Parameter:
    - external_id: Die externe ID des Alerts (aus list_alerts oder search_feed)
    - text: Der zu speichernde Text (Analyse, Befund, Handlungsempfehlung)

    Nutze dieses Tool nach einer detaillierten Incident-Analyse, damit andere
    (und du selbst in einer späteren Session) auf die Erkenntnisse zugreifen können.
    SCHREIBOPERATION — nur nach Bestätigung durch den Nutzer ausführen."""
    import uuid as _uuid
    from app.models.workflow import AlertComment

    if not external_id or not text:
        return {"ok": False, "error": "external_id und text sind erforderlich"}

    async with (await _get_db_session()) as db:
        db.add(AlertComment(
            id=_uuid.uuid4(),
            external_id=external_id,
            user_id=None,
            user_name="Computer (KI)",
            kind="ai",
            body=text[:2000],
        ))
        await db.commit()

    log.info("post_alert_comment: saved AI comment for %s", external_id)
    return {"ok": True, "external_id": external_id}


# ── Tool 9: Create Feed Exclusion ─────────────────────────────────

@mcp.tool()
async def create_feed_exclusion(
    name: str,
    query_string: str,
    source: str = "",
) -> dict:
    """Legt eine Feed-Ausnahme an: Alerts die dem Query entsprechen werden dauerhaft
    aus dem Haupt-Feed ausgeblendet (Whitelist/Suppress-Regel).

    Parameter:
    - name: Kurzer, beschreibender Name der Ausnahme (z.B. 'Backup-Jobs auf backup01')
    - query_string: OpenSearch Lucene-Query zum Matchen der auszublendenden Alerts
                   Beispiele:
                   'title:*backup* AND metadata.host:backup01*'
                   'source:graylog AND title:*connection refused*'
                   'severity:low AND metadata.host:testserver*'
    - source: Optionale Quell-Einschränkung (checkmk/graylog/wazuh/icinga2/coroot).
              Leer lassen = gilt für alle Quellen (cs-feed-*).

    Nutze dieses Tool wenn der Nutzer sagt:
    - 'Blende diese Meldung dauerhaft aus'
    - 'Erstelle eine Ausnahme für ...'
    - 'Suppress diese Alerts'
    - 'Ignoriere Alerts von Host X'

    Hinweis: Die Ausnahme wird sofort aktiv und blendet matching Alerts im Feed aus.
    Du kannst den query_string sorgfältig aus dem Kontext des betreffenden Alerts ableiten."""
    from app.models.workflow import FeedSearch

    source = source.strip().lower()
    valid_sources = {"checkmk", "graylog", "wazuh", "icinga2", "coroot"}
    if source and source not in valid_sources:
        return {"error": f"Ungültige Quelle '{source}'. Erlaubt: {', '.join(sorted(valid_sources))}"}

    index_pattern = f"cs-feed-{source}" if source else "cs-feed-*"
    query_string = query_string.strip()
    if not query_string:
        return {"error": "query_string darf nicht leer sein"}

    async with (await _get_db_session()) as db:
        search = FeedSearch(
            user_id=None,
            index_pattern=index_pattern,
            name=name.strip() or "Hermes-Ausnahme",
            query_string=query_string,
            enabled=True,
            is_system=True,
            is_exclusion=True,
            position=97,
        )
        db.add(search)
        await db.commit()
        await db.refresh(search)

    log.info("create_feed_exclusion: '%s' query='%s' index='%s'", name, query_string, index_pattern)
    return {
        "ok": True,
        "id": str(search.id),
        "name": search.name,
        "query_string": query_string,
        "index_pattern": index_pattern,
        "message": f"Ausnahme '{search.name}' wurde angelegt. Matching Alerts werden im Feed nicht mehr angezeigt.",
    }


@mcp.tool()
async def get_coroot_status(project: str = "") -> dict:
    """Gibt Coroot-Übersicht zurück: aktive Incidents und betroffene Anwendungen.

    Parameter:
    - project: optionaler Projektname-Filter (z.B. 'cue-prod', 'cue-stage').
               Leer lassen für alle konfigurierten Projekte.

    Nützlich wenn der Nutzer fragt:
    - 'Was sagt Coroot?' / 'Gibt es APM-Alerts?'
    - 'Welche Anwendungen haben gerade Probleme?'
    - 'Gibt es Latenz- oder Verfügbarkeitsprobleme?'"""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.coroot import CorootConnector

    async with (await _get_db_session()) as db:
        result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "coroot",
                ConnectorConfig.enabled.is_(True),
            )
        )
        connectors = result.scalars().all()

    if not connectors:
        return {"error": "Kein aktiver Coroot-Connector konfiguriert"}

    all_incidents: list[dict] = []
    errors: list[str] = []

    for cfg in connectors:
        try:
            creds = decrypt_credentials(cfg.encrypted_credentials)
            svc = CorootConnector(base_url=cfg.base_url, credentials=creds)
            incidents = await svc.get_incidents()
            if project:
                incidents = [i for i in incidents
                             if i["metadata"].get("project_name", "").lower() == project.lower()]
            all_incidents.extend(incidents)
        except Exception as exc:
            errors.append(f"{cfg.name}: {exc}")
            log.warning("get_coroot_status connector %s: %s", cfg.name, exc)

    by_project: dict[str, list] = {}
    for inc in all_incidents:
        p = inc["metadata"].get("project_name", "unknown")
        by_project.setdefault(p, []).append({
            "severity":    inc["severity"],
            "application": inc["metadata"].get("application", "?"),
            "description": inc["metadata"].get("short_description", "?"),
            "impact":      inc["metadata"].get("impact", 0),
            "since":       inc["metadata"].get("opened_at", ""),
            "external_id": inc["external_id"],
        })

    return {
        "total_incidents": len(all_incidents),
        "by_project": by_project,
        "errors": errors if errors else None,
    }


# ── Tool 11: IT-AIKB Knowledge Base ───────────────────────────────

@mcp.tool()
async def search_knowledge_base(query: str, deepsearch: bool = False) -> dict:
    """Durchsucht die interne IT-Wissensdatenbank (IT-AIKB / Confluence KB).

    Enthält Runbooks, Server-Dokumentation, Abhängigkeiten, Konfigurationsanleitungen
    und KB-Artikel für alle ippen.media-Systeme.

    Parameter:
    - query: Suchbegriff, z.B. 'HAProxy Konfiguration', '[KB] docker50 Abhängigkeiten',
             'Graylog Filebeat Setup', 'cue-integrations Schnittstellen'
    - deepsearch: False (Standard) = schnelle OpenSearch-Treffer mit Textauszügen
                  True = LLM-gestützte Antwort mit Quellenangaben (langsamer, ~20-60s)

    Nützlich wenn:
    - Du die Dokumentation/Konfiguration eines Servers suchst
    - Du Abhängigkeiten zwischen Diensten nachschlagen willst
    - Du Runbooks oder Anleitungen für bekannte Probleme brauchst
    - Du dir bei einer technischen Lösung unsicher bist und interne Doku prüfen willst
    """
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.aikb import AIKBConnector

    async with (await _get_db_session()) as db:
        result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "aikb",
                ConnectorConfig.enabled.is_(True),
            )
        )
        conn = result.scalar_one_or_none()

    if not conn:
        return {"error": "Kein aktiver IT-AIKB Connector konfiguriert"}

    creds = decrypt_credentials(conn.encrypted_credentials)
    aikb = AIKBConnector(base_url=conn.base_url, credentials=creds)

    if deepsearch:
        res = await aikb.search_rag(query, deepsearch=True)
        answer = res.get("answer") or ""
        sources = res.get("results") or []
        return {
            "mode": "deepsearch",
            "query": query,
            "answer": answer,
            "sources": [
                {
                    "title": s.get("title", ""),
                    "space": s.get("space_key", ""),
                    "url": s.get("source_url", ""),
                    "excerpt": s.get("content", "")[:4000],
                }
                for s in sources
            ],
        }
    else:
        hits = await aikb.search_opensearch(query, size=5)
        return {
            "mode": "opensearch",
            "query": query,
            "results": [
                {
                    "title": h.get("title", ""),
                    "space": h.get("space_key", ""),
                    "url": h.get("source_url", ""),
                    "excerpt": h.get("content", "")[:4000],
                }
                for h in hits
            ],
            "count": len(hits),
        }


# ── Remediation / AWX tools ──────────────────────────────────────

@mcp.tool()
async def list_remediations(status: str = "proposed") -> dict:
    """List AWX remediation proposals generated by the AI agent.

    Args:
        status: Filter by status — proposed, running, succeeded, failed, rejected (default: proposed).
    """
    from sqlalchemy import select as sa_select
    from app.models.remediation import RemediationProposal

    async with AsyncSessionLocal() as db:
        q = sa_select(RemediationProposal).order_by(RemediationProposal.created_at.desc())
        if status != "all":
            q = q.where(RemediationProposal.status == status)
        result = await db.execute(q.limit(50))
        rows = result.scalars().all()

    return {
        "count": len(rows),
        "remediations": [
            {
                "id": str(r.id),
                "host": r.host,
                "finding": r.finding_title,
                "template": r.awx_template_name,
                "risk": r.risk,
                "status": r.status,
                "awx_job_id": r.awx_job_id,
            }
            for r in rows
        ],
    }


@mcp.tool()
async def list_awx_templates() -> dict:
    """List available AWX job templates from the configured AWX connector."""
    from sqlalchemy import select as sa_select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.awx import AWXConnector

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            sa_select(ConnectorConfig).where(
                ConnectorConfig.type == "awx",
                ConnectorConfig.enabled.is_(True),
            ).limit(1)
        )
        cfg = result.scalar_one_or_none()

    if not cfg:
        return {"error": "No AWX connector configured"}

    creds = decrypt_credentials(cfg.encrypted_credentials)
    awx = AWXConnector(base_url=cfg.base_url, credentials=creds)
    templates = await awx.list_job_templates()
    return {
        "count": len(templates),
        "templates": [{"id": t["id"], "name": t["name"], "description": t.get("description", "")} for t in templates],
    }


@mcp.tool()
async def run_remediation(remediation_id: str) -> dict:
    """Approve and execute an AWX remediation proposal.

    SCHREIBOPERATION — nur nach Bestätigung des Nutzers ausführen.
    This launches an Ansible job on the target host. Ensure the user
    has reviewed the finding, template, extra_vars, and risk level.

    Args:
        remediation_id: UUID of the RemediationProposal to execute.
    """
    import uuid as _uuid
    from sqlalchemy import select as sa_select
    from app.models.remediation import RemediationProposal
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.awx import AWXConnector
    import asyncio
    from datetime import datetime, timezone

    async with AsyncSessionLocal() as db:
        rid = _uuid.UUID(remediation_id)
        r = (await db.execute(sa_select(RemediationProposal).where(RemediationProposal.id == rid))).scalar_one_or_none()
        if not r:
            return {"error": "Remediation not found"}
        if r.status not in ("proposed",):
            return {"error": f"Cannot approve from status '{r.status}'"}

        result = await db.execute(
            sa_select(ConnectorConfig).where(
                ConnectorConfig.type == "awx", ConnectorConfig.enabled.is_(True)
            ).limit(1)
        )
        cfg = result.scalar_one_or_none()
        if not cfg:
            return {"error": "No AWX connector configured"}

        creds = decrypt_credentials(cfg.encrypted_credentials)
        awx = AWXConnector(base_url=cfg.base_url, credentials=creds)
        launched = await awx.launch(r.awx_template_id, extra_vars=r.extra_vars or {})

        r.status = "running"
        r.awx_job_id = launched.get("job")
        r.approved_at = datetime.now(timezone.utc)
        await db.commit()

    from app.api.remediation import _poll_job
    asyncio.ensure_future(_poll_job(remediation_id, launched.get("job"), awx))

    return {
        "ok": True,
        "awx_job_id": launched.get("job"),
        "message": f"Job launched. Monitor via list_remediations('{remediation_id}').",
    }


# ── GitLab tools ──────────────────────────────────────────────────

async def _get_gitlab_connector(user_id: str | None = None):
    """Load the first enabled GitLab connector (user-owned if user_id given, else any)."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.gitlab import GitLabConnector

    async with AsyncSessionLocal() as db:
        q = select(ConnectorConfig).where(
            ConnectorConfig.type == "gitlab",
            ConnectorConfig.enabled.is_(True),
        )
        if user_id:
            q = q.where(ConnectorConfig.owner_user_id == user_id)
        result = await db.execute(q.limit(1))
        cfg = result.scalar_one_or_none()
        if not cfg:
            return None
        creds = decrypt_credentials(cfg.encrypted_credentials)
        return GitLabConnector(base_url=cfg.base_url, credentials=creds)


@mcp.tool()
async def gitlab_list_projects(search: str = "") -> dict:
    """List GitLab projects the configured PAT has access to.

    Args:
        search: Optional keyword to filter projects by name.
    """
    gl = await _get_gitlab_connector()
    if not gl:
        return {"error": "No GitLab connector configured"}
    projects = await gl.list_projects(search=search)
    return {
        "count": len(projects),
        "projects": [{"id": p["id"], "name": p["name_with_namespace"], "web_url": p.get("web_url")} for p in projects],
    }


@mcp.tool()
async def gitlab_get_file(project: str, path: str, ref: str = "main") -> dict:
    """Read a file from a GitLab repository.

    Args:
        project: Numeric project ID or URL-encoded path (e.g. "group/repo").
        path: File path within the repository (e.g. "README.md").
        ref: Branch, tag, or commit SHA (default: main).
    """
    gl = await _get_gitlab_connector()
    if not gl:
        return {"error": "No GitLab connector configured"}
    import base64
    data = await gl.get_file(project, path, ref)
    content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace") if data.get("encoding") == "base64" else data.get("content", "")
    return {"file_name": data.get("file_name"), "ref": ref, "content": content[:4000]}


@mcp.tool()
async def gitlab_list_merge_requests(project: str, state: str = "opened") -> dict:
    """List merge requests for a GitLab project.

    Args:
        project: Numeric project ID or URL-encoded path.
        state: Filter by state: opened, closed, merged, all.
    """
    gl = await _get_gitlab_connector()
    if not gl:
        return {"error": "No GitLab connector configured"}
    mrs = await gl.list_merge_requests(project, state=state)
    return {
        "count": len(mrs),
        "merge_requests": [{"iid": m["iid"], "title": m["title"], "state": m["state"], "web_url": m.get("web_url")} for m in mrs],
    }


@mcp.tool()
async def gitlab_list_pipelines(project: str, ref: str = "main") -> dict:
    """List recent pipelines for a GitLab project branch.

    Args:
        project: Numeric project ID or URL-encoded path.
        ref: Branch or tag name (default: main).
    """
    gl = await _get_gitlab_connector()
    if not gl:
        return {"error": "No GitLab connector configured"}
    pipes = await gl.list_pipelines(project, ref=ref)
    return {
        "count": len(pipes),
        "pipelines": [{"id": p["id"], "status": p["status"], "ref": p.get("ref"), "web_url": p.get("web_url")} for p in pipes],
    }


@mcp.tool()
async def gitlab_create_branch(project: str, branch: str, ref: str = "main") -> dict:
    """Create a new branch in a GitLab repository.

    SCHREIBOPERATION — nur nach Bestätigung des Nutzers ausführen.

    Args:
        project: Numeric project ID or URL-encoded path.
        branch: Name of the new branch.
        ref: Source branch or commit to branch from (default: main).
    """
    gl = await _get_gitlab_connector()
    if not gl:
        return {"error": "No GitLab connector configured"}
    result = await gl.create_branch(project, branch, ref)
    return {"name": result.get("name"), "commit": result.get("commit", {}).get("id")}


@mcp.tool()
async def gitlab_create_merge_request(project: str, source_branch: str, target_branch: str, title: str) -> dict:
    """Open a merge request in a GitLab project.

    SCHREIBOPERATION — nur nach Bestätigung des Nutzers ausführen.

    Args:
        project: Numeric project ID or URL-encoded path.
        source_branch: Branch to merge from.
        target_branch: Branch to merge into (e.g. main).
        title: MR title.
    """
    gl = await _get_gitlab_connector()
    if not gl:
        return {"error": "No GitLab connector configured"}
    mr = await gl.create_merge_request(project, source_branch, target_branch, title)
    return {"iid": mr.get("iid"), "title": mr.get("title"), "web_url": mr.get("web_url"), "state": mr.get("state")}


# ── Living Documentation ───────────────────────────────────────────

@mcp.tool()
async def store_knowledge(
    kind: str,
    title: str,
    problem: str = "",
    solution: str = "",
    service: str = "",
    host: str = "",
    tags: list[str] = [],
    confidence: float = 0.8,
    session_id: str = "",
) -> dict:
    """Speichert eine Erkenntnis in der Living Documentation (cs-knowledge OpenSearch-Index).

    Nutze dieses Tool proaktiv wenn du:
    - Ein Problem gelöst hast das wieder auftreten könnte (kind="lesson")
    - Service-Abhängigkeiten identifiziert hast (kind="dependency")
    - Ein wiederkehrendes Muster erkannt hast (kind="pattern")
    - Eine bewährte Vorgehensweise dokumentieren möchtest (kind="runbook")

    Speichere NUR verifizierte Erkenntnisse. confidence < 0.5 → nicht speichern.

    kind: "lesson"|"dependency"|"pattern"|"runbook"
    confidence: 0.0–1.0 (wie sicher bist du dir?)
    """
    from app.services.knowledge_index import store_knowledge as _store
    doc_id = await _store({
        "kind": kind, "title": title, "problem": problem,
        "solution": solution, "service": service, "host": host,
        "tags": tags, "confidence": confidence,
        "source": "hermes", "session_id": session_id,
    })
    return {"stored": True, "id": doc_id, "kind": kind, "title": title}


@mcp.tool()
async def search_knowledge(
    query: str,
    kind: str = "",
    service: str = "",
    limit: int = 5,
) -> list[dict]:
    """Sucht in der Living Documentation nach bekannten Lösungen und Erkenntnissen.

    Rufe dies auf BEVOR du ein Problem untersuchst — vielleicht wurde es schon gelöst.
    Gibt Erkenntnisse mit Confidence-Score zurück, höchste zuerst.

    kind: Optional Filter: "lesson"|"dependency"|"pattern"|"runbook"
    service: Optional Filter auf einen bestimmten Service (z.B. "graylog")
    """
    from app.services.knowledge_index import search_knowledge as _search
    results = await _search(
        query=query,
        kind=kind or None,
        service=service or None,
        limit=limit,
    )
    return results


# ── Skill-Bibliothek ───────────────────────────────────────────────

@mcp.tool()
async def list_skills(tag: str = "") -> list[dict]:
    """Zeigt alle verfügbaren öffentlichen Skills mit Name und Beschreibung.

    Rufe dies auf wenn du eine komplexe Aufgabe beginnst — vielleicht gibt es
    bereits einen bewährten Ablauf (Skill) der dir sagt wie du vorgehen sollst.

    Returns: Liste von {name, title, description, tags, version}
    """
    from app.services.knowledge_index import list_skills as _list
    return await _list(tag=tag, include_private=False)


@mcp.tool()
async def get_skill(name: str) -> dict:
    """Lädt den vollständigen Inhalt eines Skills (Prozedur/Anleitung).

    name: der Slug-Name aus list_skills (z.B. "ssh-diagnose", "docker-restart-sequence")
    Returns: {name, title, description, content, tags, version} oder {} wenn nicht gefunden.
    """
    from app.services.knowledge_index import get_skill as _get
    result = await _get(name=name)
    return result or {}


@mcp.tool()
async def store_skill(
    name: str,
    title: str,
    description: str,
    content: str,
    tags: list[str] = [],
    version: str = "1.0",
) -> dict:
    """Legt einen neuen Skill oder eine neue Version eines Skills in der Skill-Bibliothek ab.

    Nutze dies nach einer erfolgreichen nicht-trivialen Lösung:
    - Mehr als 3 Schritte
    - Wird wahrscheinlich wieder gebraucht
    - Nicht trivial zu wiederholen ohne Dokumentation

    name: Eindeutiger Slug (z.B. "graylog-restart-sequence", "opensearch-reindex")
    description: 1–2 Sätze — wann und wofür diesen Skill nutzen?
    content: Vollständige Anleitung in Markdown
    """
    from app.services.knowledge_index import store_skill as _store
    return await _store(
        name=name, title=title, description=description,
        content=content, tags=tags, version=version,
        author="hermes", user_id="", visibility="public",
    )


@mcp.tool()
async def update_knowledge(
    doc_id: str,
    title: str = "",
    problem: str = "",
    solution: str = "",
    confidence: float = 0.0,
    tags: list[str] = [],
) -> dict:
    """Aktualisiert eine bestehende Erkenntnis in der Living Documentation (Patch).

    Nur die übergebenen, nicht-leeren Felder werden überschrieben.
    doc_id: die 'id' aus search_knowledge-Ergebnissen.

    Nutze dies wenn:
    - Eine Lösung sich geändert hat oder die bisherige falsch/unvollständig war
    - Du mehr Details zu einer bekannten Erkenntnis ergänzen möchtest
    Workflow: search_knowledge(...) → doc_id ermitteln → update_knowledge(doc_id, ...)
    """
    from app.services.knowledge_index import update_knowledge as _upd
    ok = await _upd(
        doc_id,
        title=title or None,
        problem=problem or None,
        solution=solution or None,
        confidence=confidence if confidence > 0 else None,
        tags=tags if tags else None,
    )
    return {"updated": ok, "id": doc_id}


@mcp.tool()
async def forget_knowledge(doc_id: str) -> dict:
    """Löscht eine einzelne Erkenntnis dauerhaft aus der Living Documentation.

    doc_id: die 'id' aus search_knowledge-Ergebnissen.
    Nur aufrufen wenn der Nutzer explizit bittet, eine Erkenntnis zu löschen/vergessen.
    """
    from app.services.knowledge_index import forget_knowledge as _forget
    ok = await _forget(doc_id)
    return {"deleted": ok, "id": doc_id}


# ── DB session helper ──────────────────────────────────────────────

async def _get_db_session():
    """Returns an async context manager for a DB session."""
    from app.core.database import AsyncSessionLocal
    return AsyncSessionLocal()
