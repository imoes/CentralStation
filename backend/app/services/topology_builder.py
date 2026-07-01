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

# The NetBox-derived node/edge skeleton is identical for every source filter —
# only the alert overlay differs. Cache it once (the expensive part: ~1800 NetBox
# objects) so per-source builds are just a cheap OpenSearch aggregation.
_skeleton: dict | None = None   # {"nodes": {id: node}, "edges": [...]}
_skeleton_ts: float = 0.0


def _max_severity(buckets: list[dict]) -> str:
    found = set(b["key"] for b in buckets)
    for sev in _SEVERITY_ORDER:
        if sev in found:
            return sev
    return "ok"


async def _build_skeleton(db: Any) -> dict:
    """Fetch the source-independent node/edge skeleton from NetBox + AIKB edges.

    Returns {"nodes": {id: node}, "edges": [...]} or {"error": ...}.
    This is the expensive part (~1800 NetBox objects) and is cached separately
    from the per-source alert overlay.
    """
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
        return {"error": "NetBox nicht konfiguriert"}

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

    _compute_layout(nodes, edges_list)
    return {"nodes": nodes, "edges": edges_list}


def _compute_layout(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Radial tree layout — each site gets its own angular sector, children fan out
    into concentric rings by type. Pure-Python, no external deps. Runs once per
    skeleton rebuild (scheduler pre-warms the cache), so the frontend can use
    ECharts layout:'none' and avoid the browser-side force simulation freeze."""
    if not nodes:
        return
    try:
        import math

        TYPE_ORDER = ["site", "cluster", "host", "vm", "service"]
        TYPE_RANK = {t: i for i, t in enumerate(TYPE_ORDER)}
        # Radial distance (px) for each depth level: site at centre, VMs outermost.
        RADII = [0, 300, 650, 1050, 1400, 1700]

        # Build parent→children map (edge goes from lower-rank type to higher-rank).
        children_of: dict[str, list[str]] = {nid: [] for nid in nodes}
        has_parent: set[str] = set()
        for e in edges:
            s, t = e.get("source"), e.get("target")
            if s not in nodes or t not in nodes:
                continue
            sr = TYPE_RANK.get(nodes[s].get("type", ""), 99)
            tr = TYPE_RANK.get(nodes[t].get("type", ""), 99)
            if sr < tr:
                children_of[s].append(t)
                has_parent.add(t)
            elif tr < sr:
                children_of[t].append(s)
                has_parent.add(s)

        roots = [nid for nid in nodes if nid not in has_parent]

        # Bottom-up leaf count for proportional sector allocation.
        leaf_count: dict[str, int] = {}
        def _leaves(nid: str) -> int:
            ch = children_of[nid]
            v = sum(_leaves(c) for c in ch) if ch else 1
            leaf_count[nid] = v
            return v
        for r in roots:
            _leaves(r)
        # Any node not reachable from roots (orphan cycle): count as 1.
        for nid in nodes:
            leaf_count.setdefault(nid, 1)

        pos: dict[str, tuple[float, float]] = {}

        def _place(nid: str, cx: float, cy: float, angle: float, sector: float, depth: int) -> None:
            r = RADII[min(depth, len(RADII) - 1)]
            pos[nid] = (cx + r * math.cos(angle), cy + r * math.sin(angle))
            ch = children_of[nid]
            if not ch:
                return
            total = sum(leaf_count[c] for c in ch)
            start = angle - sector * 0.45
            for c in ch:
                frac = leaf_count[c] / total
                child_sector = sector * frac
                _place(c, cx, cy, start + child_sector / 2, child_sector * 0.9, depth + 1)
                start += child_sector

        # Distribute roots around a virtual centre.
        total_root_leaves = sum(leaf_count[r] for r in roots)
        start_angle = 0.0
        for r in roots:
            frac = leaf_count[r] / max(total_root_leaves, 1)
            sector = 2 * math.pi * frac
            _place(r, 0.0, 0.0, start_angle + sector / 2, sector * 0.9, 0)
            start_angle += sector

        # Disconnected nodes (not reachable from any root).
        orphans = [nid for nid in nodes if nid not in pos]
        for j, nid in enumerate(orphans):
            a = 2 * math.pi * j / max(len(orphans), 1)
            pos[nid] = (1800 * math.cos(a), 1800 * math.sin(a))

        for nid, (x, y) in pos.items():
            node = nodes.get(nid)
            if node is not None:
                node["x"] = round(x, 1)
                node["y"] = round(y, 1)

        log.info("topology: precomputed radial layout for %d nodes", len(nodes))
    except Exception as e:
        log.warning("topology: layout precompute failed (frontend will fall back): %s", e)


async def _get_skeleton(db: Any, force_refresh: bool) -> dict:
    """Return the cached NetBox+AIKB skeleton, rebuilding when stale or forced."""
    global _skeleton, _skeleton_ts
    if (not force_refresh and _skeleton is not None
            and (time.monotonic() - _skeleton_ts) < _CACHE_TTL):
        return _skeleton
    skel = await _build_skeleton(db)
    if "error" not in skel:
        _skeleton = skel
        _skeleton_ts = time.monotonic()
    return skel


async def build_topology(db: Any, force_refresh: bool = False, source_filter: str | None = None) -> dict:
    global _cache, _cache_ts
    cache_key = source_filter or ""

    if not force_refresh and cache_key in _cache and (time.monotonic() - _cache_ts.get(cache_key, 0)) < _CACHE_TTL:
        return _cache[cache_key]

    skeleton = await _get_skeleton(db, force_refresh)
    if "error" in skeleton:
        return {"nodes": [], "edges": [], "stats": {}, "error": skeleton["error"]}

    # Fresh copy of the node dicts so the per-source alert overlay never mutates
    # the shared skeleton (status/alert_count are reset per source).
    nodes: dict[str, dict] = {nid: dict(n) for nid, n in skeleton["nodes"].items()}
    edges_list = skeleton["edges"]

    # ── Alert overlay from OpenSearch (the only source-dependent part) ─────────
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
                # Only actionable severities (warning+ → medium/high/critical); info/low
                # are non-actionable noise and must never colour the map.
                "must_not": [
                    {"term": {"status": "resolved"}},
                    {"terms": {"severity": ["info", "low"]}},
                    *excl,
                ],
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
        # Pre-index nodes by short name for the fallback match (avoids an O(N) scan
        # per bucket — with ~1900 nodes × ~2000 buckets that was millions of ops).
        short_index: dict[str, str] = {}
        for nid in nodes:
            short_index.setdefault(nid.split(".")[0], nid)
        for bucket in buckets:
            host_key: str = (bucket.get("key") or "").lower()
            count: int = bucket.get("doc_count", 0)
            sev_buckets = bucket.get("sev", {}).get("buckets", [])
            status = _max_severity(sev_buckets)
            target_id = host_key if host_key in nodes else short_index.get(host_key.split(".")[0])
            if target_id:
                nodes[target_id]["status"] = status
                nodes[target_id]["alert_count"] = count
    except Exception as e:
        log.warning("topology: alert overlay failed (continuing without): %s", e)

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


# Source filters the UI offers, plus None (= all sources). Used by the scheduler
# to pre-warm every view.
_PREWARM_SOURCES: list[str | None] = [None, "checkmk", "graylog", "wazuh", "icinga2", "coroot"]


async def refresh_all_caches(db: Any) -> None:
    """Scheduler entry point: rebuild the shared NetBox skeleton once, then
    recompute every per-source alert overlay so all cached views stay warm and
    fresh. The per-source loop only costs one cheap OpenSearch aggregation each.
    """
    global _cache, _cache_ts
    await _get_skeleton(db, force_refresh=True)  # rebuild skeleton a single time
    for src in _PREWARM_SOURCES:
        key = src or ""
        # Drop the stale result so build_topology recomputes the overlay against
        # the freshly rebuilt skeleton instead of returning the cached copy.
        _cache.pop(key, None)
        _cache_ts.pop(key, None)
        await build_topology(db, force_refresh=False, source_filter=src)


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

    pages = await aikb.search_opensearch("Abhängigkeiten", size=200)
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
