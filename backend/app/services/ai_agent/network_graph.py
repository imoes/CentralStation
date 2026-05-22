"""Network Agent — LangGraph workflow for switch event analysis.

Nodes:
  collect_switch_logs → Graylog: source nsa*/nss*/nsc*
  enrich_switches     → ID-Generator: switch name → location
  analyze_network     → Qwen: STP/LACP/port-flapping analysis
  act                 → persist NetworkSwitchEvents, push WS
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm_client import generate_text

log = logging.getLogger(__name__)

NETWORK_SYSTEM = """Du bist ein erfahrener Netzwerktechniker. Analysiere die folgenden Switch-Log-Einträge und identifiziere:

1. Netzwerkprobleme: Port-Flapping, STP-Topology-Changes, LACP-Fehler, MAC-Flooding, Link-Down-Ereignisse
2. Affected Devices: Betroffene Switches, Ports, VLANs
3. Standort-Zusammenhänge: Gruppenweise Probleme an einem Standort
4. Hersteller-spezifische Fehlerbilder (Juniper Syslog-Patterns)

Antworte AUSSCHLIESSLICH im JSON-Format:
{
  "severity": "critical|high|medium|low|info",
  "problems": [
    {
      "switch": "NSA001",
      "type": "port_flapping|stp_change|lacp_error|link_down|mac_flood|other",
      "description": "...",
      "location": "...",
      "affected_port": "..."
    }
  ],
  "summary": "Kurze Zusammenfassung auf Deutsch"
}"""


async def collect_switch_logs(state: dict, db: Any) -> dict:
    from sqlalchemy import select
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "graylog",
            ConnectorConfig.enabled.is_(True),
        )
    )
    graylog_conn = result.scalars().first()
    if not graylog_conn:
        return {**state, "switch_events": []}

    creds = decrypt_credentials(graylog_conn.encrypted_credentials)
    from app.services.connectors.graylog import GraylogConnector
    svc = GraylogConnector(base_url=graylog_conn.base_url, credentials=creds)
    try:
        events = await svc.get_switch_events(time_range_seconds=3600)
    except Exception as e:
        log.warning("collect_switch_logs: %s", e)
        events = []

    return {**state, "switch_events": events}


async def enrich_switches(state: dict, db: Any) -> dict:
    events = state.get("switch_events", [])
    if not events:
        return state

    from sqlalchemy import select
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "id_generator",
            ConnectorConfig.enabled.is_(True),
        )
    )
    idgen_conn = result.scalars().first()
    if not idgen_conn:
        return state

    creds = decrypt_credentials(idgen_conn.encrypted_credentials)
    from app.services.connectors.id_generator import IDGeneratorConnector
    idgen_svc = IDGeneratorConnector(base_url=idgen_conn.base_url, credentials=creds)

    # Cache switch → location lookups
    location_cache: dict[str, dict | None] = {}

    enriched: list[dict] = []
    for event in events:
        switch_name = event.get("switch_name", "")
        if switch_name not in location_cache:
            try:
                location_cache[switch_name] = await idgen_svc.resolve_switch_to_location(switch_name)
            except Exception:
                location_cache[switch_name] = None
        loc = location_cache.get(switch_name)
        enriched.append({
            **event,
            "location_id": loc.get("location_id") if loc else None,
            "location_name": loc.get("location_name") if loc else None,
            "location_city": loc.get("location_city") if loc else None,
        })

    return {**state, "switch_events": enriched}


async def analyze_network(state: dict, llm_config: Any) -> dict:
    events = state.get("switch_events", [])
    if not events:
        return {**state, "network_analysis": None}

    if not llm_config.is_configured:
        return {**state, "network_analysis": None}

    events_text = "\n".join(
        f"[{e.get('switch_name','?')}] [{e.get('vendor','?')}] "
        f"{e.get('location_name','?')}: {e.get('message','')[:150]}"
        for e in events[:50]
    )

    try:
        raw = await generate_text(
            llm_config,
            [
                {"role": "system", "content": NETWORK_SYSTEM},
                {"role": "user", "content": f"Switch-Log-Ereignisse der letzten Stunde:\n{events_text}"},
            ],
            temperature=0.1,
            reasoning_effort="low",
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        analysis = json.loads(raw)
    except Exception as e:
        log.error("analyze_network: %s", e)
        analysis = {"severity": "info", "problems": [], "summary": str(e)}

    return {**state, "network_analysis": analysis}


async def act_network(state: dict, db: Any) -> dict:
    events = state.get("switch_events", [])
    analysis = state.get("network_analysis")

    # Persist new switch events
    from app.models.network import NetworkSwitchEvent
    for event in events[:100]:
        dedup_key = event.get("dedup_key", "")
        if dedup_key:
            from sqlalchemy import select
            existing = await db.execute(
                select(NetworkSwitchEvent).where(
                    NetworkSwitchEvent.dedup_key == dedup_key
                )
            )
            if existing.scalar_one_or_none():
                continue

        severity = "info"
        if analysis:
            problems = analysis.get("problems", [])
            switch = event.get("switch_name", "")
            for p in problems:
                if p.get("switch") == switch:
                    ptype = p.get("type", "")
                    if ptype in ("port_flapping", "stp_change", "lacp_error"):
                        severity = "high"
                    elif ptype == "link_down":
                        severity = "medium"
                    break

        db.add(NetworkSwitchEvent(
            switch_name=event.get("switch_name", ""),
            switch_type=event.get("switch_type", "nsa"),
            location_id=event.get("location_id"),
            location_name=event.get("location_name"),
            location_city=event.get("location_city"),
            vendor=event.get("vendor", "Unknown"),
            message=event.get("message", "")[:1000],
            severity=severity,
            graylog_message_id=event.get("id"),
            dedup_key=dedup_key,
            status="new",
        ))

    await db.commit()

    # Push via WebSocket to network_technician/admin clients
    if events:
        try:
            from app.api.ws import manager
            await manager.broadcast(
                {
                    "type": "network_event",
                    "switch_count": len(events),
                    "severity": analysis.get("severity", "info") if analysis else "info",
                    "summary": analysis.get("summary", "") if analysis else "",
                },
                roles=["admin", "network_technician"],
            )
        except Exception as e:
            log.warning("act_network: WS broadcast failed: %s", e)

    return state


async def run_network_workflow(db: Any) -> dict:
    from app.services.settings import get_llm_config
    llm_config = await get_llm_config(db)

    state: dict = {"switch_events": [], "network_analysis": None}
    state = await collect_switch_logs(state, db)
    if not state["switch_events"]:
        log.info("run_network_workflow: no switch events found")
        return state
    state = await enrich_switches(state, db)
    state = await analyze_network(state, llm_config)
    state = await act_network(state, db)
    return state
