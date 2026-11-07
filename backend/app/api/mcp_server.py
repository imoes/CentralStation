"""CentralStation FastMCP Server — exposes IT-Ops tools for Hermes.

Hermes configures this as an MCP server and can call these tools to query
alerts, check hosts, search logs, acknowledge alerts, and create tickets.

Mounted at /api/mcp in main.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from fastmcp import FastMCP

log = logging.getLogger(__name__)

mcp = FastMCP("CentralStation IT-Ops", version="1.0.0")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_os():
    from app.core.opensearch import get_opensearch
    return get_opensearch()  # sync singleton, no await


# ── Zustimmungspflicht für Schreiboperationen ───────────────────────────────
#
# Ein Hinweis im Prompt ("frage vorher nach") ist eine Bitte, kein Schutz — das
# Modell kann ihn überlesen, und genau das ist passiert: Tickets wurden ohne
# Rückfrage kommentiert. Schreibende Werkzeuge sind deshalb gesperrt, solange der
# Mensch in der CentralStation-Konsole kein Zeitfenster geöffnet hat.
#
# Dieselbe Freigabe steuert bereits den Bash-Guard im Nutzercontainer
# (userenv/cs-readonly-guard.py). Es gibt bewusst nur EINEN Freigabekanal und
# EINEN Speicherort dafür: /opt/cs-write-approval.json im Container des Nutzers,
# root-owned, vom Agenten nicht beschreibbar. Zustimmung im Chat zählt nicht —
# der Agent kontrolliert den Chat, nicht aber diese Datei.
#
# Grenze, ausdrücklich benannt: der MCP-Endpunkt ist im internen Netz nicht
# authentifiziert, die Nutzerkennung kommt als Kopfzeile vom Client. Das schützt
# gegen einen übereifrigen Agenten, nicht gegen einen böswilligen — ein solcher
# könnte die Jira-API auch direkt per HTTP ansprechen. Der Zweck ist, dass
# *versehentliches* Handeln unmöglich wird.

#: Kopfzeile mit der CentralStation-Nutzer-ID. Gleicher Name wie beim
#: Agent-Credentials-Endpunkt (computer_proxy.py) — ein Ding, ein Name.
CS_USER_HEADER = "x-cs-user-id"


def _calling_user_id() -> str | None:
    """CentralStation-Nutzer-ID des aufrufenden MCP-Clients — nur mit gültigem Agent-Token.

    Ohne passendes X-CS-Agent-Token gilt der Aufrufer als nicht identifiziert: eine
    selbst erklärte ID allein ist keine Identität (siehe security.agent_token).
    """
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers(include_all=True) or {}
    except Exception:
        return None
    from app.core.security import AGENT_TOKEN_HEADER, verify_agent_token
    value = (headers.get(CS_USER_HEADER) or "").strip()
    if not value:
        return None
    if not verify_agent_token(value, headers.get(AGENT_TOKEN_HEADER)):
        log.warning("MCP call with X-CS-User-ID %s but missing/invalid agent token — "
                    "treated as unidentified", value[:8])
        return None
    return value


async def _write_allowed() -> tuple[bool, str]:
    """(erlaubt, Begründung). Begründung ist nur bei Verweigerung gefüllt."""
    user_id = _calling_user_id()
    if not user_id:
        return False, (
            "Der Aufrufer ist nicht identifizierbar (keine Nutzerkennung im "
            "MCP-Aufruf), deshalb lässt sich keine Freigabe prüfen."
        )
    try:
        from app.services.userenv_manager import get_write_approval
        granted = await asyncio.to_thread(get_write_approval, user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("write approval check failed for %s: %s", user_id, exc)
        return False, f"Die Schreibfreigabe konnte nicht geprüft werden ({exc})."
    if granted:
        return True, ""
    return False, "Es ist derzeit kein Schreib-Zeitfenster geöffnet."


async def _require_write(action: str) -> dict | None:
    """None, wenn erlaubt — sonst die Ablehnung, die das Werkzeug zurückgibt."""
    allowed, why = await _write_allowed()
    if allowed:
        return None
    log.info("MCP-Schreiboperation abgelehnt: %s (%s)", action, why)
    return {
        "ok": False,
        "error": "Zustimmung erforderlich",
        "reason": why,
        "action": action,
        "next_step": (
            f"Diese Aktion ({action}) verändert etwas nach außen und wurde NICHT "
            "ausgeführt. Eine Zustimmung im Chat genügt dafür nicht — sie ist hier "
            "nicht sichtbar. Beschreibe dem Nutzer, was du vorhast (bei einem "
            "Kommentar: den vollständigen Text im Wortlaut, bei einem Statuswechsel: "
            "Ziel-Status und Ticket) und bitte ihn, in der CentralStation-Konsole "
            "\"Schreibzugriff freigeben\" zu klicken. Danach kannst du die Aktion "
            "im geöffneten Zeitfenster erneut aufrufen."
        ),
    }


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

    Nutze dieses Tool wenn der Nutzer nach aktuellen Problemen oder Alerts fragt.

    ABGRENZUNG zu vibemk (CheckMK-MCP): Nimm IMMER dieses Tool für Alerts. Es sieht
    alle Quellen (CheckMK + Graylog + Wazuh) und trägt den CentralStation-Zustand,
    den CheckMK gar nicht kennt: Quittierung, KI-Analyse, Ausschlüsse, Jira-Bezug,
    Dedup. `vibemk_get_current_problems` kennt nur CheckMK und nur den AUGENBLICK —
    es gibt dort keine Alert-Historie ('was war letzten Dienstag'). Für die Historie
    ist `search_feed` zuständig."""
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
                # Full body — no cap. A truncated body hides the very detail the agent
                # needs (e.g. the exact line/column of a YAML parser error).
                "body": h["_source"].get("body", "") or "",
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
    denied = await _require_write("Alert quittieren")
    if denied:
        return denied
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

# CheckMK live vitals live in app.services.checkmk_metrics — one place for both the
# MCP tools and the AI agent, so there is no second implementation to drift.
from app.services.checkmk_metrics import (  # noqa: E402
    fetch_host_metrics as _fetch_host_performance,
    checkmk_configs as _checkmk_configs_svc,
    host_status as _host_status,
)


async def _checkmk_configs():
    """All enabled CheckMK connector configs + credentials."""
    async with (await _get_db_session()) as db:
        return await _checkmk_configs_svc(db)


# ── ID-Generator (Stammdaten: Standorte, Firmen, Netze, Server) — nur lesend ──────

import re as _re  # noqa: E402

_IDGEN_NAME = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_IDGEN_IP = _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_IDGEN_RESOURCE = _re.compile(r"^[a-z][a-z0-9_]{1,63}$")
#: Nur EINE lesende Anweisung: SELECT … ohne Semikolon und ohne Datei-/Wartefunktionen.
_IDGEN_SQL_FORBIDDEN = _re.compile(r";|\binto\s+(out|dump)file\b|\bload_file\b|\bsleep\s*\(|\bbenchmark\s*\(",
                                   _re.I)


async def _idgen_connector():
    from sqlalchemy import select
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig
    from app.services.connectors.id_generator import IDGeneratorConnector
    async with (await _get_db_session()) as db:
        cfg = (await db.execute(select(ConnectorConfig).where(
            ConnectorConfig.type == "id_generator", ConnectorConfig.enabled.is_(True)))).scalars().first()
    if cfg is None:
        return None
    return IDGeneratorConnector(base_url=cfg.base_url,
                                credentials=decrypt_credentials(cfg.encrypted_credentials))


@mcp.tool()
async def lookup_idgen_host(name: str) -> dict:
    """Standort eines Hosts, einer IP-Adresse oder eines Switches aus dem ID-Generator.

    Parameter:
    - name: Hostname/FQDN (z. B. 'vpp0221.example.com'), IPv4-Adresse oder Switch (NSA…/NSS…/NSC…)

    Liefert location_id, Standortname und Stadt. Hosts werden über die Tabelle virt_servers
    gesucht, sonst über DNS und das passende IP-Netz (ip_networks)."""
    conn = await _idgen_connector()
    if conn is None:
        return {"error": "Kein ID-Generator-Connector konfiguriert"}
    name = (name or "").strip()
    if not (_IDGEN_IP.match(name) or _IDGEN_NAME.match(name)):
        return {"error": "Ungültiger Name — erlaubt: Hostname, FQDN, IPv4-Adresse oder Switch-Name"}
    try:
        if _IDGEN_IP.match(name):
            loc, how = await conn.resolve_ip_to_location(name), "IP-Netz"
        elif name[:3].lower() in ("nsa", "nss", "nsc"):
            loc, how = await conn.resolve_switch_to_location(name), "Switch-Tabelle"
        else:
            loc, how = await conn.resolve_host_to_location(name), "virt_servers bzw. DNS + IP-Netz"
    except Exception as exc:  # noqa: BLE001
        return {"error": f"ID-Generator nicht erreichbar: {str(exc)[:200]}"}
    if not loc:
        return {"name": name, "found": False, "searched": how}
    return {"name": name, "found": True, "via": how, **loc}


@mcp.tool()
async def list_idgen_records(resource: str, filter: str = "", limit: int = 25) -> dict:
    """Datensätze einer ID-Generator-Tabelle (Stammdaten), nur lesend.

    Parameter:
    - resource: Tabelle, z. B. 'locations', 'companies', 'companygroups', 'ip_networks',
      'virt_servers', 'switchnsadevices'
    - filter: optional 'feld=wert' (Gleichheit), mehrere mit '&', z. B. 'status=active&city=München'
    - limit: Anzahl (Standard 25, höchstens 500)"""
    conn = await _idgen_connector()
    if conn is None:
        return {"error": "Kein ID-Generator-Connector konfiguriert"}
    resource = (resource or "").strip().lower()
    if not _IDGEN_RESOURCE.match(resource):
        return {"error": "Ungültiger Tabellenname (nur Kleinbuchstaben, Ziffern, _)"}
    filters = {}
    for part in (filter or "").split("&"):
        k, sep, v = part.partition("=")
        if sep and _IDGEN_RESOURCE.match(k.strip().lower()):
            filters[k.strip()] = v.strip()
    limit = max(1, min(int(limit or 25), 500))
    try:
        rows = await conn.list_records(resource, filters, limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"ID-Generator: {str(exc)[:200]}", "resource": resource}
    return {"resource": resource, "filter": filters, "count": len(rows), "rows": rows[:limit]}


@mcp.tool()
async def query_idgen_sql(sql: str, limit: int = 50) -> dict:
    """Lesende SQL-Abfrage (MySQL) gegen die ID-Generator-Datenbank.

    Parameter:
    - sql: genau EINE SELECT-Anweisung, z. B.
      "SELECT v.name, l.name AS standort FROM virt_servers v LEFT JOIN locations l ON v.location_id = l.id WHERE v.name LIKE 'vpp02%'"
    - limit: Anzahl Zeilen (Standard 50, höchstens 500)

    Nur SELECT; mehrere Anweisungen, Datei- und Wartefunktionen werden abgelehnt."""
    conn = await _idgen_connector()
    if conn is None:
        return {"error": "Kein ID-Generator-Connector konfiguriert"}
    q = (sql or "").strip()
    if not q.lower().startswith("select") or _IDGEN_SQL_FORBIDDEN.search(q):
        return {"error": "Nur eine einzelne SELECT-Anweisung ist erlaubt (kein ';', keine "
                         "Datei- oder Wartefunktionen)."}
    limit = max(1, min(int(limit or 50), 500))
    try:
        rows = await conn.sql_select(q, limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"ID-Generator: {str(exc)[:300]}"}
    return {"count": len(rows), "rows": rows[:limit]}


@mcp.tool()
async def get_checkmk_host(hostname: str) -> dict:
    """Ruft den CheckMK-Status eines Hosts ab: alle Services + aktuelle Performance-Metriken (CPU, RAM, Disk).

    Parameter:
    - hostname: Hostname oder FQDN (z.B. 'docker086' oder 'docker086.example.com')

    Nutze dieses Tool wenn der Nutzer den Status eines bestimmten Servers wissen will.

    ABGRENZUNG zu vibemk (CheckMK-MCP): Dieses Tool liefert den Überblick über EINEN
    Host — alle Services plus die Standardmetriken CPU/RAM/Disk der letzten 2 Stunden.
    Für eine ANDERE Metrik, ein längeres Zeitfenster oder eine feinere Zeitreihe nimm
    `vibemk_get_service_metrics` (jede Metrik, bis 30 Tage, 1-Minuten-Auflösung bis 4h)."""
    return await _host_status(hostname)


# ── Tool: Get CheckMK Performance Metrics (RRD) ───────────────────────────────

@mcp.tool()
async def get_checkmk_performance(hostname: str, hours: int = 2) -> dict:
    """Ruft frische RRD-Performance-Metriken direkt aus CheckMK ab (CPU, RAM, Disk).

    Immer live von CheckMK — kein Cache. Liefert aktuellen Wert, Min/Max im Zeitfenster
    und Trend-Richtung (↑ steigend / → stabil / ↓ fallend).

    Parameter:
    - hostname: Hostname (z.B. 'docker086' oder 'docker086.example.com')
    - hours:    Zeitfenster in Stunden für Trend-Berechnung (Standard: 2)

    Nutze dieses Tool für den SCHNELLEN Blick auf die Standardmetriken eines Hosts
    beim Analysieren eines Alerts — ein Aufruf, fertig zusammengefasster Trend.

    ABGRENZUNG zu vibemk (CheckMK-MCP): Dieses Tool ist bewusst eng — feste Metriken
    (CPU/RAM/Disk), Trend statt Zeitreihe. Sobald du eine ANDERE Metrik brauchst, die
    Rohwerte sehen willst oder über 24h hinausgehst, nimm `vibemk_get_service_metrics`:
    jede Metrik, bis 30 Tage, mit `reduce` (max/min/average).

    ACHTUNG Auflösung (gemessen an load1): CheckMK konsolidiert und liefert für JEDEN
    Zeitraum ~300–360 Punkte — 1 Min bei 1–4h, 5 Min bei 24h, 30 Min bei 7d, 120 Min
    bei 30d. Für kurze Fenster ist die Live-Abfrage also am feinsten; für lange
    Fenster sind die Werte Mittelwerte, keine Spitzen."""
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
    benannte Muster siehe analyze_hostgroup_patterns.

    ABGRENZUNG zu vibemk (CheckMK-MCP): Hier liegt der Unterschied nicht in den
    Rohdaten (beides kommt live aus CheckMKs RRD), sondern im Zuschnitt: dieses Tool
    aggregiert eine GANZE Hostgruppe über mehrere Zeitfenster und rechnet Fleet-
    Aggregate, Cross-Metrik-Korrelationen und Peak-Cluster aus. `vibemk_get_service_metrics`
    liefert dagegen EINEN Host/Service — nimm es zum Nachbohren, wenn dieses Tool
    einen auffälligen Host benannt hat."""
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
    Tage'. Das Ergebnis enthält benannte Muster + die zugrundeliegende Evidenz.

    ABGRENZUNG zu vibemk (CheckMK-MCP): Das kann vibemk NICHT — dort gibt es keine
    gruppenweite Musterkennung und keine Verknüpfung mit Graylog-Logs. Umgekehrt:
    sobald ein Muster einen konkreten Host/Service benennt, hol die Detail-Zeitreihe
    mit `vibemk_get_service_metrics`."""
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
async def create_jira_ticket(
    title: str,
    description: str,
    priority: str = "medium",
    project: str | None = None,
    assignee: str | None = None,
    issue_type: str | None = None,
    target: str | None = None,
) -> dict:
    """Erstellt ein Jira- oder ServiceDesk-Ticket.

    Parameter:
    - title: Kurze Zusammenfassung (max 200 Zeichen)
    - description: Ausführliche Beschreibung
    - priority: critical/high/medium/low (Standard: medium)
    - project: Projektschlüssel, z. B. "IMSP" oder "IMIT". Ohne Angabe: Standardprojekt.
      Die Instanz (Jira oder ServiceDesk) wird anhand des Projekts selbst gefunden.
    - assignee: Bearbeiter — Nachname, Login oder E-Mail (z. B. "Scholtysik"). Gesucht
      wird nur unter den im Projekt zuweisbaren Personen; ist der Name mehrdeutig, kommt
      die Kandidatenliste zurück statt eines Tickets.
    - issue_type: Vorgangstyp; ohne Angabe ein passender, den es im Projekt gibt.
    - target: "Jira" oder "ServiceDesk" (auch "SD"). Sagt der Nutzer, wo das Ticket hin
      soll, IMMER setzen — das Ziel ist dann bindend, es wird nicht auf die andere Instanz
      ausgewichen. Gibt es das Projekt dort nicht, kommt die Projektliste dieses Ziels
      zurück: dann den Nutzer fragen, welches Projekt.

    Nutze dieses Tool wenn der Nutzer ein Ticket oder eine Aufgabe erstellen möchte."""
    denied = await _require_write("Jira-Ticket anlegen")
    if denied:
        return denied
    import json as _json
    from sqlalchemy import select
    from app.models.settings import GlobalSetting
    from app.models.user import User
    from app.services.ticketing import create_jira_issue, find_connector_for_project, normalize_target

    user_id = _calling_user_id()
    async with (await _get_db_session()) as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none() \
            if user_id else None
        if user is None:
            return {"ok": False, "error": "Aufrufer nicht identifizierbar — Ticket nicht angelegt."}

        if not project:
            for key in ("jira.default_project", "jira.ticket_project"):
                row = (await db.execute(select(GlobalSetting).where(GlobalSetting.key == key))
                       ).scalar_one_or_none()
                if row and row.value_plain:
                    project = row.value_plain
                    break
            project = project or "IMIT"
        project = project.strip().upper()

        # Wo liegt das Projekt? Vorher nahm das Werkzeug den erstbesten Connector — im
        # Zweifel den ServiceDesk, auf dem es IMSP gar nicht gibt.
        wanted, why = normalize_target(target)
        if why:
            return {"ok": False, "error": why}
        connector_type, why = await find_connector_for_project(db, user, project, wanted)
        if not connector_type:
            return {"ok": False, "error": why}

        # Prioritätsnamen sind instanzabhängig; jira.priority_map überschreibt die Standardnamen.
        prio_map = {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low"}
        map_row = (await db.execute(
            select(GlobalSetting).where(GlobalSetting.key == "jira.priority_map")
        )).scalar_one_or_none()
        if map_row and map_row.value_plain:
            try:
                prio_map.update(_json.loads(map_row.value_plain))
            except Exception:
                log.warning("jira.priority_map is not valid JSON, using defaults")

        result = await create_jira_issue(
            db, user, connector_type=connector_type, project=project,
            summary=title[:200], description=description,
            priority=prio_map.get((priority or "medium").lower(), prio_map["medium"]),
            issue_type=issue_type or "", assignee=assignee,
        )
        if result.get("ok"):
            result["project"] = project
            result["target"] = connector_type
        return result


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
    # Schreibt in die Timeline, die das ganze Team sieht — bis 2026-10-11 ohne Prüfung,
    # obwohl der Docstring "nur nach Bestätigung" sagt.
    denied = await _require_write("KI-Kommentar an einem Alert speichern")
    if denied:
        return denied

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

    # Blendet Alerts dauerhaft aus — eine der folgenreichsten Aktionen überhaupt, bis
    # 2026-10-11 ohne Freigabe-Prüfung.
    denied = await _require_write("Feed-Ausnahme anlegen")
    if denied:
        return denied

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
async def get_coroot_status(project: str = "", service: str = "") -> dict:
    """Gibt Coroot-Observability zurück: Incidents, degradierte Anwendungen (APM),
    proaktive Risiken und — für einen konkreten Dienst — seine eBPF-Abhängigkeiten.

    Coroot liefert die APPLIKATIONSEBENE (Request-Latenz, Fehlerraten, Restarts,
    Service-zu-Service-Abhängigkeiten), die CheckMK NICHT sieht. Host-Metriken
    (CPU/RAM/Disk) bewusst NICHT enthalten — dafür get_checkmk_host/-performance nutzen.

    Parameter:
    - project: optionaler Projektname-Filter (z.B. 'cue-prod'). Leer = alle Projekte.
    - service: optionaler Dienst-/Hostname (z.B. 'cue-router.cue.example.com').
               Wenn gesetzt, werden dessen Up-/Downstream-Abhängigkeiten zurückgegeben.

    Nützlich wenn der Nutzer fragt:
    - 'Was sagt Coroot?' / 'Welche Anwendungen haben Latenz-/Fehlerprobleme?'
    - 'Wer hängt von Dienst X ab?' / 'Welche Dienste nutzt X?' (→ service-Parameter)
    - 'Welche Risiken erkennt Coroot?' (Single-Instance, unreplizierte DB, …)"""
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
    degraded_apps: list[dict] = []
    all_risks: list[dict] = []
    service_deps: dict | None = None
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
            degraded_apps.extend(await svc.get_application_health())
            all_risks.extend(await svc.get_risks())
            if service and service_deps is None:
                for a in await svc.get_service_map():
                    if a["app"].lower() == service.lower():
                        service_deps = {
                            "service": a["app"],
                            "status": a["status"],
                            "used_by": [d["to"] for d in a.get("downstreams", [])],
                            "depends_on": [u["to"] for u in a.get("upstreams", [])],
                        }
                        break
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

    # Risks grouped by type (proactive inspections, no CheckMK equivalent).
    from collections import Counter as _Counter
    risk_groups = _Counter(
        f"{(r.get('key') or {}).get('type','?')} ({(r.get('key') or {}).get('category','')})"
        for r in all_risks
    )

    return {
        "total_incidents": len(all_incidents),
        "by_project": by_project,
        # APM degraded applications (latency/errors/restarts/logs) — top 20.
        "degraded_apps": [
            {"app": a["app"], "type": a.get("type", ""), "status": a["status"],
             "signals": a.get("signals", {})}
            for a in sorted(degraded_apps,
                            key=lambda x: 0 if x.get("status") == "critical" else 1)[:20]
        ],
        "degraded_apps_total": len(degraded_apps),
        "risks_summary": [{"type": k, "count": v} for k, v in risk_groups.most_common(10)],
        "service_dependencies": service_deps,
        "errors": errors if errors else None,
    }


# ── Tool 11: IT-AIKB Knowledge Base ───────────────────────────────

@mcp.tool()
async def search_knowledge_base(query: str, deepsearch: bool = False) -> dict:
    """Durchsucht die interne IT-Wissensdatenbank (IT-AIKB / Confluence KB).

    Enthält Runbooks, Server-Dokumentation, Abhängigkeiten, Konfigurationsanleitungen
    und KB-Artikel für alle example.com-Systeme.

    Parameter:
    - query: Suchbegriff, z.B. 'HAProxy Konfiguration', '[KB] docker50 Abhängigkeiten',
             'Graylog Filebeat Setup', 'cue-integrations Schnittstellen'
    - deepsearch: False (Standard) = schnelle OpenSearch-Treffer mit Textauszügen
                  True = LLM-gestützte Antwort mit Quellenangaben. Aufwendig: rund
                  120 LLM-Aufrufe je Anfrage, gemessen ~280 s, erlaubt bis 900 s.
                  Warte das Ergebnis ab — ein Abbruch liefert nichts.

    Nützlich wenn:
    - Du die Dokumentation/Konfiguration eines Servers suchst
    - Du Abhängigkeiten zwischen Diensten nachschlagen willst
    - Du Runbooks oder Anleitungen für bekannte Probleme brauchst
    - Du dir bei einer technischen Lösung unsicher bist und interne Doku prüfen willst
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.aikb import AIKBConnector, load_aikb_connector, search_kb

    if not deepsearch:
        # dieselbe schnelle Suche wie im KI-Trichter der Log-Überwachung (search_kb)
        async with (await _get_db_session()) as db:
            return await search_kb(db, query, size=5)

    # Dieselbe Abfrage, nach der die Agenten-Anleitung entscheidet, ob sie dieses
    # Werkzeug überhaupt erwähnt (load_aikb_connector) — eine Definition für beides.
    async with (await _get_db_session()) as db:
        conn = await load_aikb_connector(db)

    if not conn:
        return {"error": "Kein aktiver IT-AIKB Connector konfiguriert"}

    creds = decrypt_credentials(conn.encrypted_credentials)
    aikb = AIKBConnector(base_url=conn.base_url, credentials=creds)

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


# ── Server-Dokumentation: Manpages und Fähigkeiten (Prüf-Container) ──────────
# Lesend per Konstruktion: es laufen nur die Vorlagen man_search / man_read /
# host_capabilities aus cs_policy, über den System-Prüfaccount, nur für Hosts aus dem
# CheckMK-Inventar, jeder Aufruf in remote_exec_log. Ohne ausgewiesenen Aufrufer
# (Agent-Token) nichts — sonst nutzte jeder, der /api/mcp erreicht, den System-Account.

def _unidentified() -> dict:
    return {"ok": False, "error": "unidentified",
            "message": "Aufrufer nicht ausgewiesen (Agent-Token fehlt) — nur aus der Konsole nutzbar."}


@mcp.tool()
async def search_manpages(host: str, query: str) -> dict:
    """Manpages auf einem SERVER nach einem Stichwort durchsuchen (apropos, Abschnitte 1 und 8).

    Zeigt, welche Befehle es auf genau diesem Host gibt und wozu sie dienen — in der dort
    installierten Version, nicht aus dem Internet. Nutze das, bevor du einen Befehl
    vorschlägst, bei dem du unsicher bist, ob er auf dem Host existiert.

    Parameter:
    - host: Hostname aus dem CheckMK-Inventar (FQDN oder eindeutiger Kurzname)
    - query: EIN Stichwort, z. B. 'rsync', 'firewall', 'zfs'
    """
    user_id = _calling_user_id()
    if not user_id:
        return _unidentified()
    from app.services.manpages import search_manpages as _search
    return await _search(host, query, user_id)


@mcp.tool()
async def read_manpage(host: str, command: str, focus: str = "", section: str = "") -> dict:
    """Die Manpage eines Befehls auf einem SERVER lesen — gekürzt auf das Wesentliche.

    Liefert NAME, SYNOPSIS und, mit `focus`, genau die passenden Optionen/Absätze (z. B.
    focus='--delete' bei rsync). Ohne focus: Anfang der Beschreibung und die Liste der
    Abschnitte. So prüfst du Optionen in der Version, die auf dem Host läuft.

    Parameter:
    - host: Hostname aus dem CheckMK-Inventar
    - command: Befehl/Manpage, z. B. 'rsync', 'systemctl'
    - focus: optional, Option oder Stichwort, z. B. '--delete', 'timeout'
    - section: optional, Manpage-Abschnitt, z. B. '8'
    """
    user_id = _calling_user_id()
    if not user_id:
        return _unidentified()
    from app.services.manpages import read_manpage as _read
    return await _read(host, command, focus, section, user_id)


@mcp.tool()
async def host_capabilities(host: str) -> dict:
    """Was auf einem SERVER vorhanden ist: OS und Version, Kernel, vorhandene Werkzeuge
    (systemctl, docker, podman, kubectl, nginx, mysql, …) und die sudo-Rechte des
    Prüfaccounts.

    Rufe das zuerst auf, wenn du einen unbekannten Host untersuchst — dann weißt du z. B.,
    ob docker überhaupt läuft oder ob journalctl verfügbar ist, statt es auszuprobieren.

    Parameter:
    - host: Hostname aus dem CheckMK-Inventar
    """
    user_id = _calling_user_id()
    if not user_id:
        return _unidentified()
    from app.services.manpages import host_capabilities as _caps
    return await _caps(host, user_id)


# ── Remediation / AWX tools ──────────────────────────────────────

@mcp.tool()
async def list_remediations(status: str = "proposed") -> dict:
    """List AWX remediation proposals generated by the AI agent.

    Args:
        status: Filter by status — proposed, running, succeeded, failed, rejected (default: proposed).
    """
    from sqlalchemy import select as sa_select
    from app.models.remediation import RemediationProposal

    async with (await _get_db_session()) as db:
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

    async with (await _get_db_session()) as db:
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
    denied = await _require_write("AWX-Remediation ausführen")
    if denied:
        return denied
    import uuid as _uuid
    from sqlalchemy import select as sa_select
    from app.models.remediation import RemediationProposal
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.awx import AWXConnector
    import asyncio
    from datetime import datetime, timezone

    async with (await _get_db_session()) as db:
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

    async with (await _get_db_session()) as db:
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
    # Full file content — no cap. Truncating hides exactly what the agent is asked to
    # find (e.g. the broken line in a long YAML).
    return {"file_name": data.get("file_name"), "ref": ref, "content": content}


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
    denied = await _require_write("GitLab-Branch anlegen")
    if denied:
        return denied
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
    denied = await _require_write("GitLab-Merge-Request anlegen")
    if denied:
        return denied
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


# ── Project MCP tools ──────────────────────────────────────────────

@mcp.tool()
async def cs_create_project(
    name: str,
    description: str = "",
    status: str = "planning",
) -> dict:
    """Erstellt ein neues Projekt in CentralStation.

    name: Projektname.
    description: Optionale Beschreibung.
    status: planning | active | done | archived (Standard: planning).
    Gibt id, name, status zurück.
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        project = await svc.create_project(db, name, description or None, owner_id=None, status=status)
        return {"id": str(project.id), "name": project.name, "status": project.status}


@mcp.tool()
async def cs_update_project(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> dict:
    """Aktualisiert Name, Beschreibung oder Status eines Projekts.

    project_id: UUID des Projekts.
    status: planning | active | done | archived.
    """
    import app.services.project_service as svc
    kwargs = {}
    if name is not None:
        kwargs["name"] = name
    if description is not None:
        kwargs["description"] = description
    if status is not None:
        kwargs["status"] = status
    async with (await _get_db_session()) as db:
        project = await svc.update_project(db, project_id, **kwargs)
        return {"id": str(project.id), "name": project.name, "status": project.status}


@mcp.tool()
async def cs_list_projects(search: str = "") -> list[dict]:
    """Listet alle CentralStation-Projekte auf.

    search: optionaler Suchbegriff für den Projektnamen.
    Gibt id, name, status, updated_at zurück.
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        projects = await svc.list_projects(db, search)
        return [
            {"id": str(p.id), "name": p.name, "status": p.status,
             "updated_at": p.updated_at.isoformat()}
            for p in projects
        ]


@mcp.tool()
async def cs_get_project_plan(project_id: str) -> dict:
    """Gibt den vollständigen Projektplan zurück: Schritte, Abhängigkeiten und kritischen Pfad.

    project_id: UUID des Projekts (aus cs_list_projects).
    Gibt project-Metadaten, steps[] mit CPM-Daten, deps[] zurück.
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        graph = await svc.get_project_graph(db, project_id)
        return graph.model_dump(mode="json")


@mcp.tool()
async def cs_add_step(
    project_id: str,
    title: str,
    description: str = "",
    jira_issue_type: str = "task",
    priority: str = "medium",
    duration_days: int = 1,
    story_points: int | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    depends_on: list[str] | None = None,
    parent_step_id: str | None = None,
) -> dict:
    """Fügt dem Projekt einen neuen Schritt hinzu.

    project_id: UUID des Projekts.
    title: Titel des Schritts.
    jira_issue_type: epic | story | task | subtask | bug.
    priority: highest | high | medium | low | lowest.
    duration_days: Geschätzte Dauer in Werktagen.
    story_points: Story-Points-Schätzung (optional).
    assignee: Name oder E-Mail des Bearbeiters.
    labels: Liste von Labels z.B. ["backend","infra"].
    depends_on: Liste von step_ids die vorher abgeschlossen sein müssen.
    parent_step_id: Übergeordneter Schritt (für Epic->Task->Subtask-Hierarchie).
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        step = await svc.add_step(
            db, project_id,
            title=title,
            description=description or None,
            jira_issue_type=jira_issue_type,
            priority=priority,
            duration_days=duration_days,
            story_points=story_points,
            assignee=assignee,
            labels=labels,
            depends_on=depends_on or [],
            parent_step_id=parent_step_id,
        )
        return {"id": str(step.id), "title": step.title, "jira_issue_type": step.jira_issue_type, "priority": step.priority}


@mcp.tool()
async def cs_update_step(
    project_id: str,
    step_id: str,
    title: str | None = None,
    description: str | None = None,
    duration_days: int | None = None,
    jira_issue_type: str | None = None,
    priority: str | None = None,
    story_points: int | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    acceptance_criteria: str | None = None,
) -> dict:
    """Aktualisiert einen Projektschritt (alle Felder optional).

    project_id: UUID des Projekts.
    step_id: UUID des Schritts.
    priority: highest | high | medium | low | lowest.
    labels: Liste von Labels – ersetzt die bestehenden Labels vollständig.
    acceptance_criteria: Definition of Done (Markdown).
    """
    import app.services.project_service as svc
    kwargs: dict = {}
    for k, v in [("title", title), ("description", description), ("duration_days", duration_days),
                 ("jira_issue_type", jira_issue_type), ("priority", priority),
                 ("story_points", story_points), ("assignee", assignee),
                 ("labels", labels), ("acceptance_criteria", acceptance_criteria)]:
        if v is not None:
            kwargs[k] = v
    async with (await _get_db_session()) as db:
        step = await svc.update_step(db, step_id, project_id, **kwargs)
        return {"id": str(step.id), "title": step.title, "status": step.status, "priority": step.priority}


@mcp.tool()
async def cs_set_step_status(project_id: str, step_id: str, status: str) -> dict:
    """Setzt den Status eines Projektschritts manuell.

    status: pending | in_progress | done
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        step = await svc.update_step(db, step_id, project_id, status=status)
        return {"id": str(step.id), "status": step.status}


@mcp.tool()
async def cs_add_dependency(
    project_id: str,
    step_id: str,
    depends_on_step_id: str,
) -> dict:
    """Fügt eine Abhängigkeit hinzu: step_id kann erst starten wenn depends_on_step_id fertig ist.

    Gibt Fehler zurück wenn ein Zyklus entstehen würde.
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        dep = await svc.add_dependency(db, project_id, step_id, depends_on_step_id)
        return {"id": str(dep.id), "step_id": step_id, "depends_on_step_id": depends_on_step_id}


@mcp.tool()
async def cs_remove_dependency(project_id: str, dep_id: str) -> dict:
    """Entfernt eine Abhängigkeit zwischen zwei Projektschritten.

    dep_id: UUID der Abhängigkeit (aus cs_get_project_plan deps[].id).
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        await svc.remove_dependency(db, dep_id, project_id)
        return {"deleted": dep_id}


@mcp.tool()
async def cs_delete_step(project_id: str, step_id: str) -> dict:
    """Löscht einen Projektschritt (inkl. aller Abhängigkeiten dieses Schritts).

    project_id: UUID des Projekts.
    step_id: UUID des Schritts.
    """
    import app.services.project_service as svc
    async with (await _get_db_session()) as db:
        await svc.delete_step(db, step_id, project_id)
        return {"deleted": step_id}


# ── DB session helper ──────────────────────────────────────────────

async def _get_db_session():
    """Returns an async context manager for a DB session."""
    from app.core.database import AsyncSessionLocal
    return AsyncSessionLocal()


# ── Jira Ticket Management ──────────────────────────────────────────────────

async def _all_jira_connectors():
    """Every enabled Jira/ServiceDesk connector, as JiraConnector instances."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.services.connectors.jira import JiraConnector
    from app.core.security import decrypt_credentials

    async with (await _get_db_session()) as db:
        result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type.in_(["jira", "jira_sd"]),
                ConnectorConfig.enabled.is_(True),
            ).order_by(ConnectorConfig.type)
        )
        cfgs = result.scalars().all()
    return [
        JiraConnector(base_url=c.base_url, credentials=decrypt_credentials(c.encrypted_credentials))
        for c in cfgs
    ]


async def _get_jira_connector():
    """First enabled Jira connector — only for calls that are not about one issue."""
    conns = await _all_jira_connectors()
    if not conns:
        return None, "Kein Jira-Connector konfiguriert"
    return conns[0], None


async def _jira_for_issue(issue_key: str):
    """Resolve the connector that actually holds this issue.

    Jira and ServiceDesk are separate instances with separate project keys. Picking
    "the first connector" made every issue on the other instance unreachable: a
    ServiceDesk key was queried against Jira and came back 404, so the tools reported
    the ticket as missing rather than looking next door.
    """
    conns = await _all_jira_connectors()
    if not conns:
        return None, "Kein Jira-Connector konfiguriert"
    errors: list[str] = []
    for c in conns:
        try:
            await c.get_issue_detail(issue_key)
            return c, None
        except Exception as exc:  # wrong instance for this key — try the next
            errors.append(f"{c.base_url}: {str(exc)[:80]}")
    return None, f"Ticket '{issue_key}' auf keiner Jira-Instanz gefunden ({'; '.join(errors)})"


@mcp.tool()
async def search_tickets(query: str, target: str = "", project: str = "",
                         open_only: bool = False, max_results: int = 20) -> dict:
    """Tickets in Jira UND ServiceDesk durchsuchen — Freitext, kein JQL nötig.

    Nimm das für "such mal Tickets zu …", "gab es dazu schon ein Ticket?", "was ist mit
    IMIT-1234?". Liefert je Treffer Schlüssel, Titel, Status, Bearbeiter, Instanz (Jira oder
    ServiceDesk), Link und die passende Textstelle. Danach Details mit jira_get_issue.

    Parameter:
    - query: Suchbegriffe (Volltext) oder ein Ticketschlüssel, z. B. 'zipline 401' / 'IMIT-1234'
    - target: optional 'jira' oder 'servicedesk' — sonst beide
    - project: optional Projektschlüssel (z. B. 'IMIT', 'IMSP'); gesucht wird nur dort, wo es
      das Projekt gibt
    - open_only: nur nicht erledigte Tickets
    - max_results: höchstens so viele Treffer (Standard 20, max. 50), neueste zuerst
    Für komplexe Filter (Bearbeiter, Zeiträume, Felder) gibt es jira_search_issues mit JQL.
    """
    from app.services.ticketing import search_tickets as _search
    try:
        async with (await _get_db_session()) as db:
            return await _search(db, _calling_user_id(), query, target=target or None,
                                 project=project or None, open_only=open_only, max_results=max_results)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def jira_search_issues(jql: str, max_results: int = 20) -> dict:
    """Sucht Jira-Tickets via JQL-Abfrage — über ALLE konfigurierten Instanzen.

    Parameter:
    - jql: JQL-Abfrage, z.B. 'project=IMIT AND assignee=currentUser() AND statusCategory != Done'
    - max_results: Max. Anzahl Ergebnisse (Standard: 20)

    Beispiele:
    - 'project=IMIT AND status="In Progress" ORDER BY updated DESC'
    - 'issueKey in (IMIT-123, IMIT-124)'
    - 'assignee=currentUser() AND statusCategory != Done ORDER BY priority DESC'

    Jira und ServiceDesk sind getrennte Instanzen mit eigenen Projekten; jedes
    Ergebnis nennt in 'instance', woher es stammt. Eine Instanz, deren JQL scheitert
    (z.B. unbekanntes Projekt), wird unter 'errors' gemeldet statt still zu fehlen.
    """
    conns = await _all_jira_connectors()
    if not conns:
        return {"ok": False, "error": "Kein Jira-Connector konfiguriert"}

    out: list[dict] = []
    errors: list[str] = []
    for connector in conns:
        try:
            issues = await connector.search_issues(
                jql,
                fields=["summary", "status", "priority", "assignee", "issuetype", "updated", "description"],
            )
        except Exception as exc:
            errors.append(f"{connector.base_url}: {str(exc)[:100]}")
            continue
        for i in issues:
            f = i.get("fields") or {}
            out.append({
                "key": i.get("key"),
                "summary": f.get("summary"),
                "status": (f.get("status") or {}).get("name"),
                "priority": (f.get("priority") or {}).get("name"),
                "assignee": (f.get("assignee") or {}).get("displayName"),
                "type": (f.get("issuetype") or {}).get("name"),
                "updated": f.get("updated"),
                "instance": connector.base_url,
            })

    if not out and errors:
        return {"ok": False, "error": "; ".join(errors)}
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    result = {"ok": True, "count": len(out[:max_results]), "issues": out[:max_results]}
    if errors:
        result["errors"] = errors
    return result

@mcp.tool()
async def jira_get_issue(issue_key: str) -> dict:
    """Gibt vollständige Details eines Jira-Tickets zurück inkl. Beschreibung und Kommentare.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234'
    """
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    try:
        detail = await connector.get_issue_detail(issue_key)
        return {"ok": True, **detail}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def jira_update_issue(
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    priority: str | None = None,
) -> dict:
    """Aktualisiert Felder eines Jira-Tickets. Nur gesetzte Parameter werden geändert.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234'
    - summary: Neue Zusammenfassung (optional)
    - description: Neue Beschreibung (optional)
    - priority: Neue Priorität, z.B. 'High', 'Medium', 'Low', 'Highest' (optional)
    """
    denied = await _require_write("Jira-Ticket ändern")
    if denied:
        return denied
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    if not any([summary, description, priority]):
        return {"ok": False, "error": "Mindestens ein Feld (summary, description, priority) muss angegeben werden"}
    try:
        await connector.update_issue(issue_key, summary=summary, description=description, priority=priority)
        updated = {k: v for k, v in {"summary": summary, "description": description, "priority": priority}.items() if v is not None}
        return {"ok": True, "issue_key": issue_key, "updated_fields": updated}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def jira_add_comment(issue_key: str, body: str, body_format: str = "markdown") -> dict:
    """Fügt einen Kommentar zu einem Jira-Ticket hinzu.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234'
    - body: Kommentartext. Markdown wird automatisch in Jira-Format übersetzt.
    - body_format: "markdown" (Standard) oder "jira" für bereits fertiges
      Jira-Wiki-Markup wie h2., {{code}} und *fett*.

    JEMANDEN ERWÄHNEN: Jira benachrichtigt nur bei `[~benutzername]` — ein '@' davor
    tut nichts, und der Anzeigename funktioniert nicht. Den Benutzernamen vorher mit
    jira_search_users nachschlagen; er unterscheidet sich je Instanz (dieselbe Person
    heißt in Jira und im ServiceDesk oft anders).

    Beispiel: "[~mmustermann] kannst du das prüfen?"

    STIL — das Ticket lesen Menschen, oft auch Externe:
    Schreibe in Fließtext, wie ein Kollege es in den Vorgang schreiben würde. Kurze
    Absätze statt Stichpunktlisten, Überschriften und Statusmarker. Sag, was du
    herausgefunden hast und was als Nächstes sinnvoll ist.

    NICHT hineinschreiben:
    - wie du an die Information gekommen bist (SSH, Werkzeuge, Konnektoren, Kommandos)
    - Betriebszustände deiner selbst: "Read-only-Analyse", "kein Schreibzugriff",
      "marvin konnte keinen Zugriff erlangen", "Tool lieferte keine Daten"
    - Kennzeichnungen wie "KI-Analyse", "automatisch erstellt", Konfidenzwerte
    Konntest du etwas nicht prüfen, formuliere es fachlich ("auf dem Host liegen dazu
    keine Logeinträge vor") oder lass es weg — die Grenzen deiner Werkzeuge sind für
    den Leser des Tickets ohne Belang.
    """
    denied = await _require_write("Ticket-Kommentar schreiben")
    if denied:
        return denied
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    if body_format not in ("markdown", "jira"):
        return {"ok": False, "error": "body_format muss 'markdown' oder 'jira' sein"}
    try:
        if body_format == "markdown":
            from app.services.connectors.jira import markdown_to_jira_wiki
            body = markdown_to_jira_wiki(body)
        comment = await connector.add_comment(issue_key, body)
        return {"ok": True, **comment}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Anhänge ─────────────────────────────────────────────────────────────────
#
# Der Agent legt Dateien in seinem Container unter /home/yolo/workspaces ab; dieser
# MCP-Server läuft im Backend. Beide sehen dasselbe Verzeichnis, weil der Host-Pfad
# IDE_WORKSPACES_BASE/<user>/workspaces in beide Container gemountet ist — der Agent
# nennt also einen Pfad, statt den Inhalt durch das Protokoll zu schieben.
#
# Welcher Nutzer das ist, kommt aus derselben Kopfzeile wie bei der Schreibfreigabe.
# Ohne sie lässt sich kein Arbeitsverzeichnis bestimmen, und das Werkzeug verweigert.

#: Obergrenze. Jira lehnt größere Uploads serverseitig ohnehin ab (Standard 10 MB),
#: aber ein klarer Fehler hier ist besser als ein HTTP 413 aus der Ferne.
_ATTACH_MAX_BYTES = 25 * 1024 * 1024

#: Wie der Pfad im Container des Agenten heißt (eine Quelle: userenv_manager).
from app.services.userenv_manager import AGENT_WORKSPACE as _AGENT_WORKSPACE


def _resolve_workspace_file(user_id: str, file_path: str) -> tuple[str | None, str]:
    """Übersetzt einen Agenten-Pfad in einen Backend-Pfad. (Pfad, Fehlergrund).

    Dünne Weiterleitung: die Regel — und damit die Sicherheitsgrenze — steht in
    userenv_manager, weil auch der Bild-Upload der Konsole sie braucht.
    """
    from app.services.userenv_manager import resolve_workspace_file
    return resolve_workspace_file(user_id, file_path)


@mcp.tool()
async def jira_add_attachment(issue_key: str, file_path: str, filename: str = "") -> dict:
    """Hängt eine Datei aus dem Arbeitsverzeichnis an ein Jira-/ServiceDesk-Ticket.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234'
    - file_path: Pfad im Arbeitsverzeichnis, z.B. '/home/yolo/workspaces/analyse.csv'
      oder kurz 'analyse.csv'. Andere Orte sind nicht erlaubt.
    - filename: optionaler Name, unter dem der Anhang im Ticket erscheint
      (Standard: der Dateiname). Nutze das für einen sprechenden Namen wie
      'disk-auslastung-2026-09.csv' statt 'out.csv'.

    ABLAUF: Datei zuerst mit Bash im Arbeitsverzeichnis erzeugen (Bericht, CSV,
    Logauszug, Diagramm), danach dieses Werkzeug aufrufen. Der Inhalt wird NICHT
    über den Chat übertragen — es wird nur der Pfad genannt.

    ABGRENZUNG: für Fließtext im Vorgang ist jira_add_comment richtig. Ein Anhang
    lohnt sich für das, was als Kommentar unlesbar wäre: lange Logauszüge, Tabellen,
    Messreihen, Bilder. Lade nichts hoch, was Zugangsdaten oder personenbezogene
    Daten enthält, die nicht ohnehin schon im Ticket stehen.

    SCHREIBOPERATION — ein Anhang ist für alle Ticket-Leser sichtbar und lässt sich
    nicht spurlos entfernen. Sie braucht die Freigabe des Nutzers.
    """
    denied = await _require_write("Ticket-Anhang hochladen")
    if denied:
        return denied

    user_id = _calling_user_id()
    if not user_id:
        return {"ok": False, "error": "Aufrufer nicht identifizierbar — "
                                      "Arbeitsverzeichnis nicht bestimmbar"}
    full, err = _resolve_workspace_file(user_id, file_path)
    if err:
        return {"ok": False, "error": err}

    size = os.path.getsize(full)
    if size == 0:
        return {"ok": False, "error": "Die Datei ist leer — nichts hochzuladen"}
    if size > _ATTACH_MAX_BYTES:
        return {"ok": False, "error": f"Datei zu groß ({size} Bytes, erlaubt sind "
                                      f"{_ATTACH_MAX_BYTES}). Kürze sie oder hänge "
                                      f"einen Auszug an."}

    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}

    import mimetypes
    name = (filename or "").strip() or os.path.basename(full)
    name = os.path.basename(name)          # kein Pfad im Anzeigenamen
    guessed = mimetypes.guess_type(name)[0] or ""
    try:
        data = await asyncio.to_thread(lambda: open(full, "rb").read())
        created = await connector.add_attachment(issue_key, name, data, guessed)
        return {"ok": True, "issue_key": issue_key, "attachments": created}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def jira_list_attachments(issue_key: str) -> dict:
    """Listet die Anhänge eines Jira-/ServiceDesk-Tickets (Name, Größe, Datum, Autor).

    Nützlich vor dem Hochladen — so hängst du nichts doppelt an — und um zu sehen,
    was der Melder dem Ticket beigelegt hat.
    """
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    try:
        items = await connector.list_attachments(issue_key)
        return {"ok": True, "issue_key": issue_key, "count": len(items),
                "attachments": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def jira_search_users(query: str, issue_key: str = "") -> dict:
    """Sucht Jira-Benutzer — für Erwähnungen in Kommentaren.

    Parameter:
    - query: Namensfragment, z.B. 'kluge' oder 'mueller'
    - issue_key: optional. Angegeben, wird nur die Instanz durchsucht, auf der das
      Ticket liegt — sonst alle.

    Warum das nötig ist: eine Erwähnung braucht `[~benutzername]`, nicht den
    Anzeigenamen und kein '@'. Die Benutzernamen unterscheiden sich zwischen Jira und
    ServiceDesk, deshalb nennt jedes Ergebnis seine 'instance' — nimm nur einen
    Treffer von der Instanz, auf der das Ticket liegt.
    """
    if issue_key:
        connector, err = await _jira_for_issue(issue_key)
        if err:
            return {"ok": False, "error": err}
        conns = [connector]
    else:
        conns = await _all_jira_connectors()
        if not conns:
            return {"ok": False, "error": "Kein Jira-Connector konfiguriert"}

    users: list[dict] = []
    errors: list[str] = []
    for c in conns:
        try:
            for u in await c.search_users(query):
                users.append({**u, "instance": c.base_url,
                              "mention": f"[~{u['username']}]"})
        except Exception as exc:
            errors.append(f"{c.base_url}: {str(exc)[:80]}")
    if not users and errors:
        return {"ok": False, "error": "; ".join(errors)}
    result = {"ok": True, "count": len(users), "users": users}
    if errors:
        result["errors"] = errors
    return result


@mcp.tool()
async def jira_transition_issue(issue_key: str, status: str) -> dict:
    """Ändert den Status/Workflow eines Jira-Tickets.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234'
    - status: Ziel-Status, z.B. 'In Progress', 'Done', 'Erledigt', 'Offen', 'In Bearbeitung'

    Bei Unsicherheit welche Status verfügbar sind: jira_get_transitions vorher aufrufen.
    """
    denied = await _require_write("Ticket-Status ändern")
    if denied:
        return denied
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    try:
        transitions = await connector.get_transitions(issue_key)
        target = (
            next((t for t in transitions if t["name"] == status), None)
            or next((t for t in transitions if t["name"].lower() == status.lower()), None)
            or next((t for t in transitions if status.lower() in t["name"].lower()), None)
        )
        if not target:
            available = [t["name"] for t in transitions]
            return {"ok": False, "error": f"Status '{status}' nicht gefunden. Verfügbar: {available}"}
        await connector.transition_issue(issue_key, target["name"])
        return {"ok": True, "issue_key": issue_key, "new_status": target["name"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


#: Transition names that mean "this work is finished". Deliberately excludes
#: discard/cancel/reject wordings — those close a ticket too, but they are a different
#: outcome and must not be chosen on the agent's own initiative.
_JIRA_CLOSE_WORDS = (
    "schließen", "schliessen", "abschließen", "abschliessen", "geschlossen",
    "erledigt", "fertig", "close", "closed", "done", "resolve", "resolved", "complete",
)
_JIRA_NOT_CLOSE_WORDS = (
    "verwerfen", "discard", "cancel", "abbrechen", "reject", "ablehnen", "duplicate",
)


@mcp.tool()
async def jira_close_issue(
    issue_key: str,
    resolution: str = "Fertig",
    comment: str = "",
    comment_format: str = "markdown",
) -> dict:
    """Schließt ein Ticket — inklusive der verpflichtenden Lösung.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234' oder 'PMLOG-1318'
    - resolution: Lösung, Standard 'Fertig'. Wird gegen die erlaubten Werte des
      Übergangs geprüft; passt sie nicht, nennt die Antwort die zulässigen Werte.
    - comment: optionaler Abschlusskommentar, wird VOR dem Schließen geschrieben
    - comment_format: "markdown" (Standard) oder "jira" für fertiges Jira-Wiki-Markup

    Wichtig: Beide Instanzen verlangen beim Schließen das Feld 'Lösung' — ein reiner
    Statuswechsel ohne sie wird mit HTTP 400 abgelehnt. Die Übergänge heißen zudem
    unterschiedlich (Jira 'Done', ServiceDesk 'Ticket schließen'), deshalb sucht dieses
    Tool ihn anhand des Namens. Verwerfen/Ablehnen zählt NICHT als Schließen und wird
    nie automatisch gewählt.
    """
    denied = await _require_write("Ticket schließen")
    if denied:
        return denied
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    if comment_format not in ("markdown", "jira"):
        return {"ok": False, "error": "comment_format muss 'markdown' oder 'jira' sein"}
    try:
        transitions = await connector.get_transitions(issue_key, with_fields=True)
        names = [t["name"] for t in transitions]
        candidates = [
            t for t in transitions
            if any(w in t["name"].lower() for w in _JIRA_CLOSE_WORDS)
            and not any(w in t["name"].lower() for w in _JIRA_NOT_CLOSE_WORDS)
        ]
        if not candidates:
            return {"ok": False, "issue_key": issue_key,
                    "error": "Kein schließender Übergang verfügbar", "available": names}
        if len(candidates) > 1:
            return {"ok": False, "issue_key": issue_key,
                    "error": "Mehrere schließende Übergänge — bitte einen auswählen",
                    "candidates": [t["name"] for t in candidates], "available": names}

        target = candidates[0]
        required = connector.required_fields(target)
        fields: dict = {}
        if "resolution" in required:
            allowed = required["resolution"]["allowed"]
            match = next((a for a in allowed if a.lower() == resolution.strip().lower()), None)
            if not match:
                return {"ok": False, "issue_key": issue_key,
                        "error": f"Lösung '{resolution}' ist für diesen Übergang nicht zulässig",
                        "allowed_resolutions": allowed, "transition": target["name"]}
            fields["resolution"] = {"name": match}

        # Any other mandatory field we cannot fill — say so instead of failing with a
        # bare 400 from Jira.
        unfilled = [v["name"] for k, v in required.items() if k not in fields]
        if unfilled:
            return {"ok": False, "issue_key": issue_key,
                    "error": f"Übergang '{target['name']}' verlangt Felder, die dieses Tool "
                             f"nicht setzen kann: {unfilled}. Nutze jira_update_issue vorab.",
                    "transition": target["name"]}

        if comment.strip():
            # Comment first: if the transition fails the note is still on the ticket,
            # whereas a comment after a failed close would never be written.
            comment_body = comment.strip()
            if comment_format == "markdown":
                from app.services.connectors.jira import markdown_to_jira_wiki
                comment_body = markdown_to_jira_wiki(comment_body)
            await connector.add_comment(issue_key, comment_body)
        await connector.transition_issue(issue_key, target["name"], fields=fields or None)
        return {"ok": True, "issue_key": issue_key, "new_status": target["name"],
                "resolution": fields.get("resolution", {}).get("name"),
                "instance": connector.base_url, "comment_added": bool(comment.strip())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

@mcp.tool()
async def jira_get_transitions(issue_key: str) -> dict:
    """Listet alle verfügbaren Status-Übergänge für ein Jira-Ticket auf.

    Parameter:
    - issue_key: Ticket-Schlüssel, z.B. 'IMIT-1234'

    Nützlich um zu prüfen welche Status-Übergänge möglich sind, bevor jira_transition_issue aufgerufen wird.
    """
    connector, err = await _jira_for_issue(issue_key)
    if err:
        return {"ok": False, "error": err}
    try:
        transitions = await connector.get_transitions(issue_key)
        return {
            "ok": True,
            "issue_key": issue_key,
            "transitions": [{"id": t["id"], "name": t["name"]} for t in transitions],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Log-Überwachung: dieselben Werkzeuge wie der KI-Trichter (services/log_watch/werkzeuge.py) ──
# Nur lesend. lw_befund (Befund ablegen) bleibt dem Trichter vorbehalten — er prüft die Belege;
# die Konsolen-KI handelt nicht und schreibt keine Befunde.

@mcp.tool()
async def lw_suche(filter: str = "", stunden: float = 24, limit: int = 20, bedeutung: str = "") -> dict:
    """Relevante Lognachrichten der Historie (Graylog, Wazuh, CheckMK, Firewall, Switches, KI-Befunde)
    suchen: Treffer (id, zeit, host, relevanz, hashtags, text, gruende) und Facetten je Hashtag-Art.

    Filtersprache: art:wert (host:docker0218, thema:oom, quelle:wazuh, netz:dmz, signal:neu,
    befund:<id>), art:(a OR b), art:wert*, !art:wert, OR, Klammern, level>=7, relevanz>=3, @Objekt,
    "freier Text". bedeutung: Text → bedeutungsähnliche Nachrichten (Vektorsuche)."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_suche(filter, stunden, max(0, min(limit, 100)), bedeutung)


@mcp.tool()
async def lw_facetten(filter: str = "", stunden: float = 24) -> dict:
    """Anzahl und Facetten (Hashtags je Art mit Anzahl) zu einem Filter — welcher Wert fällt auf?"""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_facetten(filter, stunden)


@mcp.tool()
async def lw_netzplan(filter: str = "", stunden: float = 24, knoten: int = 15) -> dict:
    """Netzplan zum Filter: Knoten = Hashtags des Ergebnisses (Host, Thema, Dienst, Regel, Netz, Befund …),
    Kante = beide an derselben Nachricht, mit Anzahl. Zeigt gemeinsames Auftreten, keine Ursache."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_netzplan(filter, stunden, max(2, min(knoten, 40)))


@mcp.tool()
async def lw_nachricht(id: str) -> dict:
    """Eine Nachricht der Historie vollständig, mit allen Originalfeldern (roh)."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_nachricht(id)


@mcp.tool()
async def lw_wissen(filter: str = "", bedeutung: str = "", stunden: float = 24 * 30) -> dict:
    """Frühere KI-Befunde und Konsolen-Wissen zu einem Filter oder nach Bedeutung."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_wissen(filter, bedeutung, stunden)


@mcp.tool()
async def lw_skill() -> dict:
    """Log-Überwachung: ZUERST lesen — Bedeutung aller Felder und Hashtag-Arten einer Nachricht (host:, thema:,
    signal:, regel:, befund:, incident: …), Signale, Relevanzstufen, Kriterien R1–R8/N0–N6 und Themen. Dazu die
    live vorkommenden Hashtag-Arten, die noch nicht dokumentiert sind."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_skill()


@mcp.tool()
async def lw_definition() -> dict:
    """Die geltende Relevanz-Definition: Kriterien R1–R8 / N0–N6, Objekte, Regeln (erste passende entscheidet)."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_definition()


@mcp.tool()
async def lw_gespraech(filter: str, tage: float = 7, sitzungen: int = 3) -> dict:
    """Gesprächsprotokolle früherer KI-Analysen zum Filter (z. B. host:cue0148.example.com): je Sitzung
    die Züge von Worker (Host-Objekt), KI, Werkzeugen und Befund — laden heißt suchen."""
    from app.services.log_watch import werkzeuge
    return await werkzeuge.lw_gespraech(filter, tage, sitzungen)
