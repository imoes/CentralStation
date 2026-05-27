"""Alert aggregation service.

Polls all enabled connectors (CheckMK, Graylog, Wazuh) and stores new alerts
in the alerts table.

Dedup strategy (per source):
  - CheckMK:  status-based — re-use existing open alert for same host:service
  - Graylog:  cooldown-based — suppress if same dedup_key seen within COOLDOWN window
  - Wazuh:    cooldown-based — suppress if same agent:rule_id seen within COOLDOWN window
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_credentials
from app.models.alert import Alert
from app.models.connector import ConnectorConfig

log = logging.getLogger(__name__)

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
_DEDUP_COOLDOWN_FALLBACK = 10  # minutes, used when settings are unavailable

# In-memory registry of CheckMK-monitored hosts and their metadata.
# Populated from get_all_hosts() each aggregation run; cumulative across runs.
# Used to decide whether Graylog/Wazuh alerts are in scope for enrichment.
_host_meta_cache: dict[str, dict] = {}


async def _fetch_checkmk_items(connector: ConnectorConfig, time_range_minutes: int = 60) -> list[dict]:  # noqa: ARG001
    """Fetch CheckMK items — raises on error so callers can track success."""
    from urllib.parse import quote
    from app.services.connectors.checkmk import CheckMKConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = CheckMKConnector(base_url=connector.base_url, credentials=creds)
    base = (connector.base_url or "").rstrip("/")
    items = await svc.get_problems()
    return [
        {
            "source": "checkmk",
            "severity": i["severity"],
            "title": f"{i['host']} — {i['service']}",
            "body": i.get("output", ""),
            "external_id": f"cmk:{i['host']}:{i['service']}",
            "external_url": (
                f"{base}/check_mk/view.py"
                f"?view_name=service&host={quote(i['host'])}&service={quote(i['service'])}"
            ) if base else None,
            "metadata": {
                **(i.get("metadata") or {}),
                "host": i["host"],
                "service": i["service"],
                "host_address": i.get("host_address", ""),
            },
        }
        for i in items
    ]


async def collect_checkmk(connector: ConnectorConfig, time_range_minutes: int = 60) -> list[dict]:
    try:
        return await _fetch_checkmk_items(connector, time_range_minutes)
    except Exception as e:
        log.warning("CheckMK collection failed: %s", e)
        return []


async def _resolve_stale_checkmk_alerts(active_ext_ids: set[str], db: AsyncSession) -> int:
    """Mark open CheckMK alerts as resolved if no longer in the active problem set.

    Only called when CheckMK was successfully polled, so an empty active_ext_ids
    legitimately means 'all problems cleared'.
    """
    from app.services import feed_index

    result = await db.execute(
        select(Alert).where(
            and_(
                Alert.source == "checkmk",
                Alert.status.in_(["new", "acknowledged"]),
                Alert.external_id.notin_(active_ext_ids),
            )
        )
    )
    stale = result.scalars().all()
    if not stale:
        return 0

    for alert in stale:
        alert.status = "resolved"
        try:
            await feed_index.update_status(str(alert.id), "checkmk", "resolved")
        except Exception:
            pass

    await db.commit()
    log.info("Freshness: resolved %d stale CheckMK alert(s)", len(stale))
    return len(stale)


async def collect_graylog(connector: ConnectorConfig, time_range_minutes: int = 60) -> list[dict]:
    from app.services.connectors.graylog import GraylogConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = GraylogConnector(base_url=connector.base_url, credentials=creds)
    try:
        exclude_switches = "NOT source:(nsa* OR nss* OR nsc*)"
        time_range_secs = time_range_minutes * 60

        # HyDE multi-query approach (ref: llm-graylog-analyse/graylog_analyzer.py:fetch_logs)
        # Query 1: Filebeat-tagged logs (hyde_relevant=true, active after Filebeat rollout)
        # Query 2: HTTP 4xx/5xx errors from Docker containers
        # Query 3: General syslog errors/warnings — always-on fallback
        msgs = await svc.search_messages_multi(
            queries=[
                f"_exists_:hyde_relevant AND hyde_relevant:true AND {exclude_switches}",
                f"http_response_code:>=400 AND _exists_:container_name AND {exclude_switches}",
                f"level:<=4 AND NOT message:uprobes AND {exclude_switches}",
            ],
            time_range_seconds=time_range_secs,
            limit_per_query=50,
        )

        severity_map = {0: "critical", 1: "critical", 2: "critical", 3: "critical",
                        4: "high", 5: "medium", 6: "low", 7: "info"}
        results = []
        for m in msgs:
            dedup_key = m.get("dedup_key", "")
            level = m.get("level", 6)
            # HTTP errors from Docker containers get severity from status code, not syslog level
            http_code = m.get("http_response_code")
            if http_code:
                level = 4 if int(http_code) >= 500 else 5
                title = f"HTTP {http_code} — {m.get('container_name') or m.get('source', '')}"
            else:
                title = m["message"][:200]
            base = (connector.base_url or "").rstrip("/")
            from urllib.parse import quote
            src_host = m.get("source", "")
            container = m.get("container_name", "")
            if container:
                graylog_url = f"{base}/search?q=container_name%3A{quote(container)}&rangetype=relative&relative=3600"
            elif src_host:
                graylog_url = f"{base}/search?q=source%3A{quote(src_host)}&rangetype=relative&relative=3600"
            else:
                graylog_url = None
            results.append({
                "source": "graylog",
                "severity": severity_map.get(level, "medium"),
                "title": title,
                "body": m["message"],
                "external_id": f"glog:{dedup_key or m['id']}",
                "external_url": graylog_url if base else None,
                "metadata": {
                    "host": src_host,
                    "host_candidates": m.get("host_candidates") or ([src_host] if src_host else []),
                    "container_name": container,
                    "vendor": m.get("vendor", ""),
                    "facility": m.get("facility", ""),
                    "hyde_relevant": m.get("hyde_relevant", False),
                    "level": level,
                    "http_response_code": http_code,
                },
            })
        return results
    except Exception as e:
        log.warning("Graylog collection failed: %s", e)
        return []


async def collect_wazuh(connector: ConnectorConfig, time_range_minutes: int = 60) -> list[dict]:
    from app.services.connectors.wazuh import WazuhConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = WazuhConnector(base_url=connector.base_url, credentials=creds)
    # Wazuh Dashboard runs on port 443 (HTTPS) on the indexer host — strip the :9200 port.
    indexer_url = (creds.get("indexer_url") or "").rstrip("/")
    if indexer_url:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(indexer_url)
        dashboard_base = urlunparse(("https", p.hostname, "", "", "", ""))
        wazuh_ui = f"{dashboard_base}/app/wazuh"
    else:
        wazuh_ui = None
    try:
        items = await svc.get_alerts(limit=100, time_range_minutes=time_range_minutes)
        return [
            {
                "source": "wazuh",
                "severity": i["severity"],
                "title": i["title"],
                "body": i.get("body", ""),
                "external_id": f"wazuh:{i['external_id']}",
                "external_url": wazuh_ui,
                "metadata": i.get("metadata", {}),
            }
            for i in items
        ]
    except Exception as e:
        log.warning("Wazuh collection failed: %s", e)
        return []


COLLECTORS = {
    "checkmk": collect_checkmk,
    "graylog": collect_graylog,
    "wazuh": collect_wazuh,
}


def _alert_passes_filter(
    meta: dict,
    locations: list[str],
    ve: list[str],
    criticality: list[str],
    os_vals: list[str],
) -> bool:
    """True if an alert's metadata matches all non-empty filter lists.

    Empty metadata field (unknown) → always passes.
    Empty filter list → no restriction on that dimension.
    """
    def _check(field: str, allowed: list[str]) -> bool:
        if not allowed:
            return True
        v = (meta.get(field) or "").strip()
        return not v or v in allowed

    return (
        _check("location",    locations)
        and _check("ve",          ve)
        and _check("criticality", criticality)
        and _check("os",          os_vals)
    )


async def _filter_enrichable_docs(docs: list[dict], db: AsyncSession) -> list[dict]:
    """Return only docs relevant to at least one user's configured CheckMK filters.

    Rules:
    - CheckMK alerts: filtered directly by the union of all users' filter prefs.
    - Graylog / Wazuh alerts: enriched only if their host is known in CheckMK
      (from the in-memory cache or OpenSearch fallback) AND that host's CheckMK
      metadata passes the filter union.  Alerts whose host is unknown to CheckMK
      are not enriched — they are outside the configured monitoring scope.

    When no user has any filter configured → CheckMK docs all pass; non-CheckMK
    docs still require the host to be known in CheckMK (but any known host passes).
    """
    from sqlalchemy import select
    from app.models.workflow import UserPreference

    result = await db.execute(select(UserPreference))
    all_prefs = result.scalars().all()

    def _union(attr: str) -> list[str]:
        vals: set[str] = set()
        for p in (all_prefs or []):
            v = getattr(p, attr, None) or []
            vals.update(str(x) for x in (v if isinstance(v, list) else [v]) if x)
        return list(vals)

    union_locations   = _union("checkmk_locations")
    union_ve          = _union("checkmk_ve")
    union_criticality = _union("checkmk_criticality")
    union_os          = _union("checkmk_os")
    no_filter         = not any([union_locations, union_ve, union_criticality, union_os])

    # Split by source so we handle lookup only when needed
    checkmk_docs = [d for d in docs if d.get("source") == "checkmk"]
    other_docs   = [d for d in docs if d.get("source") != "checkmk"]

    # ── CheckMK docs ───────────────────────────────────────────────────────────
    if no_filter:
        relevant_checkmk = checkmk_docs
    else:
        relevant_checkmk = [
            d for d in checkmk_docs
            if _alert_passes_filter(
                d.get("metadata") or {},
                union_locations, union_ve, union_criticality, union_os,
            )
        ]

    if not other_docs:
        return relevant_checkmk

    # ── Graylog / Wazuh docs — host must be known in CheckMK ──────────────────
    # Collect ALL host candidates across non-CheckMK docs.
    # For Graylog/Docker messages, host_candidates contains (in priority order):
    #   source (GELF field = Docker daemon host), hostname, host_name, beat_hostname.
    # We look up the whole candidate set at once so one OpenSearch query suffices.
    def _candidates(doc: dict) -> list[str]:
        meta = doc.get("metadata") or {}
        cands = list(meta.get("host_candidates") or [])
        primary = meta.get("host", "")
        if primary and primary not in cands:
            cands.insert(0, primary)
        return [c for c in cands if c]

    all_candidate_hostnames: set[str] = {
        h for doc in other_docs for h in _candidates(doc)
    }

    # Build host→metadata map: in-memory cache first, OpenSearch for cache misses
    host_meta: dict[str, dict] = {
        h: _host_meta_cache[h] for h in all_candidate_hostnames if h in _host_meta_cache
    }
    cache_misses = all_candidate_hostnames - host_meta.keys()
    if cache_misses:
        from app.services.feed_index import get_hosts_metadata
        host_meta.update(await get_hosts_metadata(list(cache_misses)))

    relevant_others: list[dict] = []
    for doc in other_docs:
        cands = _candidates(doc)
        if not cands:
            continue  # no hostname at all → skip

        # Walk candidates in order; use the first one known in CheckMK
        matched_meta: dict | None = None
        for candidate in cands:
            if candidate in host_meta:
                matched_meta = host_meta[candidate]
                break

        if matched_meta is None:
            continue  # no candidate found in CheckMK → out of scope → skip

        if no_filter or _alert_passes_filter(matched_meta, union_locations, union_ve, union_criticality, union_os):
            relevant_others.append(doc)

    return relevant_checkmk + relevant_others


async def run_aggregation(db: AsyncSession) -> int:
    """Poll all enabled connectors, persist new alerts, return count of new alerts."""
    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type.in_(list(COLLECTORS.keys())),
            ConnectorConfig.enabled.is_(True),
        )
    )
    connectors = result.scalars().all()

    # Cooldown window — synchronized with the agent interval from settings.
    # Graylog + Wazuh suppress re-inserts of the same dedup_key within this window.
    # CheckMK uses status-based dedup (open alert = no re-insert).
    try:
        from app.services.settings import get_agent_config
        agent_cfg = await get_agent_config(db)
        cooldown_minutes = agent_cfg.interval_minutes
    except Exception:
        cooldown_minutes = _DEDUP_COOLDOWN_FALLBACK
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
    _COOLDOWN_SOURCES = {"graylog", "wazuh"}

    new_count = 0
    new_alerts: list[Alert] = []
    new_ext_urls: list[str | None] = []  # parallel list: external_url per new alert
    checkmk_active_ext_ids: set[str] = set()
    checkmk_had_successful_poll = False

    for connector in connectors:
        if connector.type == "checkmk":
            try:
                items = await _fetch_checkmk_items(connector, time_range_minutes=cooldown_minutes)
                checkmk_had_successful_poll = True
                for item in items:
                    if ext_id := item.get("external_id"):
                        checkmk_active_ext_ids.add(ext_id)
                    # Seed host cache from problem hosts (always available)
                    hostname = (item.get("metadata") or {}).get("host", "")
                    if hostname:
                        _host_meta_cache[hostname] = item.get("metadata") or {}
                # Refresh full host inventory so non-problem hosts are also cacheable
                try:
                    from app.services.connectors.checkmk import CheckMKConnector
                    _svc = CheckMKConnector(
                        base_url=connector.base_url,
                        credentials=decrypt_credentials(connector.encrypted_credentials),
                    )
                    for h in await _svc.get_all_hosts():
                        _host_meta_cache.setdefault(h["hostname"], h["metadata"])
                except Exception as exc:
                    log.debug("CheckMK full host scan skipped: %s", exc)
            except Exception as exc:
                log.warning("CheckMK collection failed: %s", exc)
                items = []
        else:
            collector = COLLECTORS.get(connector.type)
            if not collector:
                continue
            items = await collector(connector, time_range_minutes=cooldown_minutes)

        for item in items:
            ext_id = item.get("external_id")
            if ext_id:
                source = item.get("source", "")
                if source in _COOLDOWN_SOURCES:
                    # Skip if the same dedup_key was seen within the cooldown window
                    existing = await db.execute(
                        select(Alert).where(
                            and_(
                                Alert.external_id == ext_id,
                                Alert.created_at >= cooldown_cutoff,
                            )
                        )
                    )
                else:
                    # CheckMK: skip while the same problem is still open
                    existing = await db.execute(
                        select(Alert).where(
                            and_(
                                Alert.external_id == ext_id,
                                Alert.status != "resolved",
                            )
                        )
                    )
                if existing.scalar_one_or_none():
                    continue

            meta = dict(item.get("metadata") or {})
            if item.get("external_url"):
                meta["external_url"] = item["external_url"]
            alert = Alert(
                source=item["source"],
                severity=item["severity"],
                title=item["title"][:512],
                body=item.get("body"),
                external_id=ext_id,
                status="new",
                metadata_=meta or None,
            )
            db.add(alert)
            new_alerts.append(alert)
            new_ext_urls.append(item.get("external_url"))
            new_count += 1

    if new_count > 0:
        # flush to get auto-generated IDs and timestamps before commit
        await db.flush()
        docs = [
            {
                "id": str(a.id),
                "type": "alert",
                "source": a.source,
                "severity": a.severity,
                "title": a.title,
                "body": a.body,
                "metadata": a.metadata_,
                "created_at": a.created_at.isoformat(),
                "status": a.status,
                "location_name": a.location_name,
                "location_city": a.location_city,
                "external_url": new_ext_urls[idx],
                "external_id": a.external_id,
            }
            for idx, a in enumerate(new_alerts)
        ]
        await db.commit()
        log.info("Aggregated %d new alerts", new_count)

        # Index new alerts in OpenSearch (best-effort)
        try:
            from app.services.feed_index import index_items
            await index_items(docs)
        except Exception as exc:
            log.warning("OpenSearch indexing failed (non-fatal): %s", exc)

        # Enrich new alerts with AI insight in the background (best-effort, only if auto_enrich=true)
        try:
            import asyncio
            from app.core.database import AsyncSessionLocal
            from app.services.settings import get_llm_config, get_agent_config

            async def _do_enrich(docs_to_enrich: list[dict]) -> None:
                from app.services.feed_enricher import enrich_batch
                async with AsyncSessionLocal() as s:
                    agent_cfg = await get_agent_config(s)
                    if not agent_cfg.auto_enrich:
                        return
                    llm_cfg = await get_llm_config(s)
                    relevant = await _filter_enrichable_docs(docs_to_enrich, s)
                if relevant:
                    asyncio.create_task(enrich_batch(relevant, llm_cfg))

            asyncio.create_task(_do_enrich(docs))
        except Exception as exc:
            log.debug("Could not schedule feed enrichment: %s", exc)

    # Freshness: resolve CheckMK alerts whose problems no longer appear in CheckMK
    if checkmk_had_successful_poll:
        try:
            await _resolve_stale_checkmk_alerts(checkmk_active_ext_ids, db)
        except Exception as exc:
            log.warning("CheckMK freshness check failed (non-fatal): %s", exc)

    return new_count
