"""Blast-Radius Service — expands a critical alert to a list of potentially affected entities.

Given a host that has a critical alert, this service:
  1. Checks if the host is a VM (via NetBox) → finds co-hosted VMs on the same physical host
  2. Finds all monitored hosts at the same location (via ID-Generator / IP-network mapping)
  3. For network alerts: finds all devices served by the affected switch

Used by the KI agent's analyze node to enrich the analysis with topological context.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def expand_blast_radius(host: str, db) -> dict:
    """Return a blast-radius dict for the given host.

    Returns:
    {
        "host": str,
        "location": str | None,
        "co_hosted_vms": [str, ...],        # other VMs on the same physical host
        "co_located_hosts": [str, ...],     # other monitored hosts at the same location
        "reason": str,                       # human-readable explanation
    }
    """
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials

    result: dict = {
        "host": host,
        "location": None,
        "co_hosted_vms": [],
        "co_located_hosts": [],
        "reason": "",
    }
    reasons: list[str] = []

    # ── Load connectors ───────────────────────────────────────────────────────
    netbox_conn = None
    idgen_conn = None

    r = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "netbox",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    nb_row = r.scalars().first()
    if nb_row:
        from app.services.connectors.netbox import NetBoxConnector
        netbox_conn = NetBoxConnector(
            base_url=nb_row.base_url,
            credentials=decrypt_credentials(nb_row.encrypted_credentials),
        )

    r2 = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "id_generator",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    idg_row = r2.scalars().first()
    if idg_row:
        from app.services.connectors.id_generator import IDGeneratorConnector
        idgen_conn = IDGeneratorConnector(
            base_url=idg_row.base_url,
            credentials=decrypt_credentials(idg_row.encrypted_credentials),
        )

    # ── 1. Resolve location ───────────────────────────────────────────────────
    location = None
    if idgen_conn:
        try:
            loc_data = await idgen_conn.resolve_host_to_location(host)
            if loc_data:
                location = loc_data.get("location_name") or loc_data.get("location_city")
                result["location"] = location
                reasons.append(f"Standort: {location}")
        except Exception as e:
            log.debug("blast_radius: location lookup failed for %s: %s", host, e)

    # ── 2. VM co-hosting via NetBox ───────────────────────────────────────────
    if netbox_conn:
        try:
            physical_host = await netbox_conn.get_vm_host(host)
            if physical_host:
                reasons.append(f"VM auf physischem Host: {physical_host}")
                # Find all other VMs on the same physical device
                all_vms = await netbox_conn.get_vms()
                co_vms = [
                    vm.get("name", "") for vm in all_vms
                    if (vm.get("device") or {}).get("name") == physical_host
                    and vm.get("name") != host
                    and vm.get("name")
                ]
                result["co_hosted_vms"] = co_vms[:10]
                if co_vms:
                    reasons.append(f"{len(co_vms)} weitere VMs auf demselben Host: {', '.join(co_vms[:3])}{'...' if len(co_vms) > 3 else ''}")
        except Exception as e:
            log.debug("blast_radius: NetBox VM lookup failed for %s: %s", host, e)

    # ── 3. Co-located hosts from cs-metrics-checkmk ──────────────────────────
    if location:
        try:
            from app.core.opensearch import get_opensearch
            from datetime import datetime, timezone, timedelta
            since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            os_client = get_opensearch()
            resp = await os_client.search(
                index="cs-metrics-checkmk",
                body={
                    "query": {"range": {"timestamp": {"gte": since}}},
                    "aggs": {"hosts": {"terms": {"field": "host", "size": 30}}},
                    "size": 0,
                },
                ignore_unavailable=True,
            )
            # All active hosts in OpenSearch metrics
            active_hosts = {
                b["key"] for b in resp.get("aggregations", {})
                .get("hosts", {}).get("buckets", [])
                if b["key"] != host
            }
            # Filter to same location via idgen
            if idgen_conn and active_hosts:
                co_located = []
                for h in list(active_hosts)[:15]:
                    try:
                        loc_data = await idgen_conn.resolve_host_to_location(h)
                        if loc_data and (
                            loc_data.get("location_name") == location
                            or loc_data.get("location_city") == location
                        ):
                            co_located.append(h)
                    except Exception:
                        pass
                result["co_located_hosts"] = co_located[:10]
                if co_located:
                    reasons.append(f"{len(co_located)} weitere Hosts am Standort {location}: {', '.join(co_located[:3])}{'...' if len(co_located) > 3 else ''}")
        except Exception as e:
            log.debug("blast_radius: co-location lookup failed: %s", e)

    result["reason"] = "; ".join(reasons) if reasons else "Keine Topologie-Daten verfügbar"
    return result


async def get_blast_radius_for_alerts(alerts: list[dict], db) -> list[dict]:
    """Run blast-radius expansion for the critical/high alerts in a list.

    Returns a list of blast-radius results (one per unique critical host, max 3).
    """
    critical_hosts = list({
        (a.get("host") or a.get("agent") or "").strip()
        for a in alerts
        if a.get("severity") in ("critical", "high")
        and (a.get("host") or a.get("agent"))
    })[:3]

    results = []
    for host in critical_hosts:
        try:
            br = await expand_blast_radius(host, db)
            results.append(br)
            log.debug(
                "blast_radius: %s → %d co-hosted, %d co-located",
                host, len(br["co_hosted_vms"]), len(br["co_located_hosts"])
            )
        except Exception as e:
            log.debug("blast_radius: failed for %s: %s", host, e)

    return results
