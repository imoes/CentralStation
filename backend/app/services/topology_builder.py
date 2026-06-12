"""Topology builder — assembles the infrastructure graph for /topology.

Nodes:  site, cluster, host, vm  (Phase 2 adds: service)
Edges:  located_in (host→site), member_of (host→cluster), runs_on (vm→host)
Status: max open alert severity per node from cs-feed-* (critical|high|medium|low|ok).
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_SEVERITY_ORDER = ["critical", "high", "medium", "low"]

_cache: dict[str, dict] = {}   # keyed by source_filter (empty string = all sources)
_cache_ts: dict[str, float] = {}
_CACHE_TTL = 1800.0  # 30 min fallback; scheduler pre-warms every N minutes


def _max_severity(buckets: list[dict]) -> str:
    found = set(b["key"] for b in buckets)
    for sev in _SEVERITY_ORDER:
        if sev in found:
            return sev
    return "ok"


async def build_topology(db: Any, force_refresh: bool = False, source_filter: str | None = None) -> dict:
    global _cache, _cache_ts
    cache_key = source_filter or ""

    if not force_refresh and cache_key in _cache and (time.monotonic() - _cache_ts.get(cache_key, 0)) < _CACHE_TTL:
        return _cache[cache_key]

    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials

    # ── Load NetBox connector ─────────────────────────────────────────────────
    r = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "netbox",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    nb_row = r.scalars().first()
    if not nb_row:
        return {"nodes": [], "edges": [], "stats": {}, "error": "NetBox nicht konfiguriert"}

    from app.services.connectors.netbox import NetBoxConnector
    nb = NetBoxConnector(
        base_url=nb_row.base_url,
        credentials=decrypt_credentials(nb_row.encrypted_credentials),
    )

    nodes: dict[str, dict] = {}
    edges_list: list[dict] = []

    def _node_id(name: str) -> str:
        return (name or "").strip().lower()

    def _add_node(nid: str, label: str, ntype: str, inactive: bool = False) -> None:
        if nid and nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, "type": ntype,
                          "status": "ok", "alert_count": 0, "inactive": inactive}

    def _add_edge(source: str, target: str, kind: str) -> None:
        if source and target and source != target:
            edges_list.append({"source": source, "target": target, "kind": kind})

    # ── Build graph from NetBox ───────────────────────────────────────────────
    try:
        devices = await nb.get_all_devices()
    except Exception as e:
        log.warning("topology: failed to fetch NetBox devices: %s", e)
        devices = []

    try:
        vms = await nb.get_all_vms()
    except Exception as e:
        log.warning("topology: failed to fetch NetBox VMs: %s", e)
        vms = []

    for dev in devices:
        name = (dev.get("name") or "").strip()
        if not name:
            continue
        nid = _node_id(name)
        inactive = (dev.get("status") or {}).get("value", "active") != "active"
        _add_node(nid, name, "host", inactive=inactive)

        site = dev.get("site")
        if site and site.get("name"):
            sid = _node_id(site["name"])
            _add_node(sid, site["name"], "site")
            _add_edge(nid, sid, "located_in")

        cluster = dev.get("cluster")
        if cluster and cluster.get("name"):
            cid = _node_id(cluster["name"])
            _add_node(cid, cluster["name"], "cluster")
            _add_edge(nid, cid, "member_of")

    for vm in vms:
        name = (vm.get("name") or "").strip()
        if not name:
            continue
        nid = _node_id(name)
        inactive = (vm.get("status") or {}).get("value", "active") != "active"
        _add_node(nid, name, "vm", inactive=inactive)

        device = vm.get("device")
        if device and device.get("name"):
            host_id = _node_id(device["name"])
            _add_edge(nid, host_id, "runs_on")
        else:
            cluster = vm.get("cluster")
            if cluster and cluster.get("name"):
                cid = _node_id(cluster["name"])
                _add_node(cid, cluster["name"], "cluster")
                _add_edge(nid, cid, "member_of")

    # ── Alert overlay from OpenSearch ─────────────────────────────────────────
    try:
        from app.core.opensearch import get_opensearch
        from app.services.feed_index import get_exclusion_must_not_clauses

        excl = await get_exclusion_must_not_clauses(db)
        os_client = get_opensearch()
        source_must: list[dict] = []
        if source_filter:
            source_must = [{"term": {"source": source_filter}}]
        body: dict = {
            "size": 0,
            "query": {"bool": {
                "must": source_must,
                "must_not": [{"term": {"status": "resolved"}}, *excl],
            }},
            "aggs": {
                "by_host": {
                    "terms": {"field": "metadata.host.keyword", "size": 2000},
                    "aggs": {"sev": {"terms": {"field": "severity", "size": 6}}},
                }
            },
        }
        index = f"cs-feed-{source_filter}" if source_filter else "cs-feed-*"
        resp = await os_client.search(index=index, body=body, ignore_unavailable=True)
        buckets = (resp.get("aggregations") or {}).get("by_host", {}).get("buckets", [])
        for bucket in buckets:
            host_key: str = (bucket.get("key") or "").lower()
            count: int = bucket.get("doc_count", 0)
            sev_buckets = bucket.get("sev", {}).get("buckets", [])
            status = _max_severity(sev_buckets)
            # Try exact match, then short-name match
            target_id: str | None = None
            if host_key in nodes:
                target_id = host_key
            else:
                short = host_key.split(".")[0]
                for nid in nodes:
                    if nid.split(".")[0] == short:
                        target_id = nid
                        break
            if target_id:
                nodes[target_id]["status"] = status
                nodes[target_id]["alert_count"] = count
    except Exception as e:
        log.warning("topology: alert overlay failed (continuing without): %s", e)

    # ── Merge AIKB dependency edges ────────────────────────────────────────────
    try:
        from app.core.opensearch import get_opensearch
        os_client = get_opensearch()
        edge_resp = await os_client.search(
            index="cs-topology-edges",
            body={"query": {"match_all": {}}, "size": 2000},
            ignore_unavailable=True,
        )
        for hit in (edge_resp.get("hits") or {}).get("hits", []):
            src = hit.get("_source") or {}
            source = _node_id(src.get("source", ""))
            target = _node_id(src.get("target", ""))
            if source and target:
                # Create service nodes if not already in graph
                if source not in nodes:
                    _add_node(source, src.get("source", source), "service")
                if target not in nodes:
                    _add_node(target, src.get("target", target), "service")
                _add_edge(source, target, "depends_on")
    except Exception as e:
        log.debug("topology: AIKB edges not available: %s", e)

    node_list = list(nodes.values())
    stats = {
        "sites": sum(1 for n in node_list if n["type"] == "site"),
        "clusters": sum(1 for n in node_list if n["type"] == "cluster"),
        "hosts": sum(1 for n in node_list if n["type"] == "host"),
        "vms": sum(1 for n in node_list if n["type"] == "vm"),
        "alerts": sum(n["alert_count"] for n in node_list),
    }

    result = {
        "nodes": node_list,
        "edges": edges_list,
        "stats": stats,
        "source_filter": source_filter,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache[cache_key] = result
    _cache_ts[cache_key] = time.monotonic()
    return result


async def ensure_topology_index() -> None:
    """Create cs-topology-edges index if it doesn't exist."""
    from app.core.opensearch import get_opensearch
    os_client = get_opensearch()
    try:
        exists = await os_client.indices.exists(index="cs-topology-edges")
        if not exists:
            await os_client.indices.create(
                index="cs-topology-edges",
                body={
                    "mappings": {
                        "properties": {
                            "source": {"type": "keyword"},
                            "target": {"type": "keyword"},
                            "relation": {"type": "keyword"},
                            "origin": {"type": "keyword"},
                            "page_title": {"type": "text"},
                            "page_url": {"type": "keyword"},
                            "updated_at": {"type": "date"},
                        }
                    }
                },
            )
            log.info("Created OpenSearch index: cs-topology-edges")
    except Exception as e:
        log.warning("topology: failed to ensure index: %s", e)


async def run_topology_kb_extraction(db: Any) -> int:
    """Extract infrastructure dependency edges from AIKB KB pages.

    Returns the number of edges upserted.
    """
    await ensure_topology_index()

    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials

    # Load AIKB connector
    r = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "aikb",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    aikb_row = r.scalars().first()
    if not aikb_row:
        log.info("topology_kb_extraction: no AIKB connector configured, skipping")
        return 0

    from app.services.connectors.aikb import AIKBConnector
    aikb = AIKBConnector(
        base_url=aikb_row.base_url,
        credentials=decrypt_credentials(aikb_row.encrypted_credentials),
    )

    pages = await aikb.search_opensearch("Abhängigkeiten", size=40)
    if not pages:
        log.info("topology_kb_extraction: no pages found for 'Abhängigkeiten'")
        return 0

    from app.services.settings import get_active_llm_config
    from app.services.llm_client import generate_text
    from app.services.dashboard.generative_designer import _strip_thinking
    from app.core.opensearch import get_opensearch

    llm_cfg = await get_active_llm_config(db)
    if not llm_cfg.is_configured:
        log.info("topology_kb_extraction: no LLM configured, skipping")
        return 0

    os_client = get_opensearch()
    total_upserted = 0
    system_prompt = (
        "Extract infrastructure dependencies from this IT knowledge-base page. "
        "Return STRICT JSON only: "
        '{"edges":[{"source":"<hostname or service>","target":"<hostname or service>",'
        '"relation":"depends_on|connects_to|backend_of"}]} '
        "Only include explicit, named systems (hostnames, service names). "
        "No prose, no invented entries. Empty list if none found."
    )

    for page in pages:
        content = (page.get("content") or "").strip()
        if not content:
            continue
        try:
            user_content = f"Page: {page.get('title', '')}\n\n{content[:4000]}"
            raw = await generate_text(
                llm_cfg,
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_content}],
            )
            clean = _strip_thinking(raw)
            lo, hi = clean.find("{"), clean.rfind("}")
            if lo < 0 or hi <= lo:
                continue
            data = _json.loads(clean[lo:hi + 1])
            edge_list = data.get("edges") or []
            for edge in edge_list:
                source = (edge.get("source") or "").strip()
                target = (edge.get("target") or "").strip()
                relation = (edge.get("relation") or "depends_on").strip()
                if not source or not target:
                    continue
                doc_id = hashlib.sha1(f"{source}|{target}|{relation}".encode()).hexdigest()[:16]
                await os_client.index(
                    index="cs-topology-edges",
                    id=doc_id,
                    body={
                        "source": source,
                        "target": target,
                        "relation": relation,
                        "origin": "aikb",
                        "page_title": page.get("title", ""),
                        "page_url": page.get("source_url", ""),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                total_upserted += 1
        except Exception as e:
            log.debug("topology_kb_extraction: failed on page '%s': %s", page.get("title"), e)

    log.info("topology_kb_extraction: upserted %d edges", total_upserted)
    return total_upserted
