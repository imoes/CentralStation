"""Feed Index Service — stores all feed items in OpenSearch.

Index naming: cs-feed-{source}  (e.g. cs-feed-checkmk, cs-feed-o365)
Retention: configurable per source via DELETE by query (daily scheduler job).
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.opensearch import get_opensearch

log = logging.getLogger(__name__)

# One index per source for independent retention policies
INDEX_PREFIX = "cs-feed"
ALL_SOURCES = ["checkmk", "graylog", "wazuh", "icinga2", "o365", "teams", "coroot"]

_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id":                  {"type": "keyword"},
            "type":                {"type": "keyword"},
            "source":              {"type": "keyword"},
            "severity":            {"type": "keyword"},
            "title":               {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "body":                {"type": "text"},
            "metadata":            {"type": "object", "dynamic": True},
            "created_at":          {"type": "date"},
            "status":              {"type": "keyword"},
            "location_name":       {"type": "keyword"},
            "location_city":       {"type": "keyword"},
            "external_url":        {"type": "keyword"},
            "external_id":         {"type": "keyword"},
            "ai_insight":          {"type": "text"},
            "alert_score":         {"type": "float"},
            # AI resolution — set when the Computer panel marks a problem as solved
            "has_ai_resolution":   {"type": "boolean"},
            "ai_resolution_text":  {"type": "text"},
            # Searchable tags auto-generated at index time (source, severity, service names, etc.)
            # Special: "ai_resolved" added when Computer marks a problem solved
            "tags":                {"type": "keyword"},
            # owner of personal items (o365, teams) — empty = shared/all-roles
            "user_id":             {"type": "keyword"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}


def _index(source: str) -> str:
    return f"{INDEX_PREFIX}-{source}"


METRICS_INDEX = "cs-metrics-checkmk"

_METRICS_MAPPING = {
    "mappings": {
        "properties": {
            "host":        {"type": "keyword"},
            "service":     {"type": "keyword"},
            "metric":      {"type": "keyword"},
            "value":       {"type": "float"},
            "unit":        {"type": "keyword"},
            "timestamp":   {"type": "date"},
            "location":    {"type": "keyword"},
        }
    },
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
}


async def ensure_indices() -> None:
    """Create indices if they don't exist; push mapping updates to existing ones."""
    os_client = get_opensearch()
    # Fields to add to existing indices (safe: OpenSearch ignores already-mapped fields)
    _mapping_update = {"properties": {
        "ai_insight":         {"type": "text"},
        "has_ai_resolution":  {"type": "boolean"},
        "ai_resolution_text": {"type": "text"},
        "tags":               {"type": "keyword"},
    }}
    for source in ALL_SOURCES:
        idx = _index(source)
        try:
            exists = await os_client.indices.exists(index=idx)
            if not exists:
                await os_client.indices.create(index=idx, body=_INDEX_MAPPING)
                log.info("Created OpenSearch index: %s", idx)
            else:
                await os_client.indices.put_mapping(index=idx, body=_mapping_update)
        except Exception as e:
            log.warning("Could not create/update index %s: %s", idx, e)

    # Metrics index
    try:
        exists = await os_client.indices.exists(index=METRICS_INDEX)
        if not exists:
            await os_client.indices.create(index=METRICS_INDEX, body=_METRICS_MAPPING)
            log.info("Created OpenSearch index: %s", METRICS_INDEX)
    except Exception as e:
        log.warning("Could not create metrics index: %s", e)


async def backfill_from_db(days: int = 7) -> int:
    """Index all recent PostgreSQL alerts that are not yet in OpenSearch.

    Called once at app startup to populate the feed for existing deployments.
    Returns the number of documents indexed.
    """
    from datetime import timedelta
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.alert import Alert

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Alert)
                .where(Alert.created_at >= cutoff)
                .order_by(Alert.created_at.desc())
                .limit(5000)
            )
            alerts = result.scalars().all()

        if not alerts:
            return 0

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
                "external_url": (a.metadata_ or {}).get("external_url"),
                "external_id": a.external_id,
            }
            for a in alerts
        ]
        await index_items(docs)
        log.info("Feed backfill: indexed %d alerts from last %d days", len(docs), days)
        return len(docs)
    except Exception as e:
        log.warning("Feed backfill failed (non-fatal): %s", e)
        return 0


# Keywords to service tag mapping — checked against title + container_name
_SERVICE_KEYWORDS: dict[str, str] = {
    "nginx":         "nginx",
    "apache":        "apache",
    "haproxy":       "haproxy",
    "varnish":       "varnish",
    "postgres":      "postgres",
    "postgresql":    "postgres",
    "mysql":         "mysql",
    "mariadb":       "mysql",
    "redis":         "redis",
    "memcached":     "memcached",
    "elasticsearch": "elasticsearch",
    "opensearch":    "opensearch",
    "kafka":         "kafka",
    "rabbitmq":      "rabbitmq",
    "docker":        "docker",
    "kubernetes":    "kubernetes",
    "k8s":           "kubernetes",
    "keycloak":      "keycloak",
    "gitlab":        "gitlab",
    "jenkins":       "jenkins",
    "grafana":       "grafana",
    "prometheus":    "prometheus",
    "jira":          "jira",
    "confluence":    "confluence",
    "cue":           "cue",
    "zipline":       "zipline",
    "logspout":      "logspout",
    "wazuh":         "wazuh",
    "checkmk":       "checkmk",
    "graylog":       "graylog",
    "ssh":           "ssh",
    "ssl":           "ssl",
    "tls":           "tls",
    "http":          "http",
    "dns":           "dns",
    "nfs":           "nfs",
    "smb":           "smb",
    "ldap":          "ldap",
    "smtp":          "smtp",
    "backup":        "backup",
    "checkpoint":    "postgres",   # PostgreSQL checkpoint messages
    "autovacuum":    "postgres",
}


def _generate_tags(item: dict) -> list[str]:
    """Derive searchable keyword tags from an alert's fields.

    Tags generated:
    - source name  (graylog, checkmk, wazuh, …)
    - severity     (critical, high, medium, low, info)
    - "docker"     when container_name is present
    - "windows"/"linux" from title keywords
    - service tags from _SERVICE_KEYWORDS matched in title + container_name
    """
    tags: set[str] = set()

    source = item.get("source", "")
    if source:
        tags.add(source)

    severity = item.get("severity", "")
    if severity:
        tags.add(severity)

    meta = item.get("metadata") or {}
    container = (meta.get("container_name") or "").lower()
    title = (item.get("title") or "").lower()
    body = (item.get("body") or "").lower()

    if container:
        tags.add("docker")

    text = f"{title} {container} {body[:200]}"

    for keyword, tag in _SERVICE_KEYWORDS.items():
        if keyword in text:
            tags.add(tag)

    if any(k in text for k in ("windows", "win-", "win2019", "win2022")):
        tags.add("windows")
    if any(k in text for k in ("linux", "debian", "ubuntu", "centos", "rhel")):
        tags.add("linux")
    if any(k in text for k in ("oom", "out of memory", "killed process")):
        tags.add("oom")
    if any(k in text for k in ("disk", "filesystem", "no space")):
        tags.add("disk")
    if any(k in text for k in ("cpu", "load average", "high load")):
        tags.add("cpu")
    if any(k in text for k in ("timeout", "timed out", "connection refused", "unreachable")):
        tags.add("network")

    return sorted(tags)


async def index_item(item: dict) -> None:
    """Index a single feed item. item must have 'id' and 'source'."""
    source = item.get("source", "unknown")
    doc_id = item.get("id") or item.get("external_id")
    if not doc_id:
        return
    os_client = get_opensearch()
    try:
        body = {**item, "tags": _generate_tags(item)}
        await os_client.index(
            index=_index(source),
            id=str(doc_id),
            body=body,
            refresh=False,
        )
    except Exception as e:
        log.warning("OpenSearch index failed for %s: %s", doc_id, e)


async def index_items(items: list[dict]) -> None:
    """Bulk index a list of feed items."""
    if not items:
        return
    from opensearchpy.helpers import async_bulk

    os_client = get_opensearch()
    actions = [
        {
            "_index": _index(item.get("source", "unknown")),
            "_id": str(item.get("id") or item.get("external_id", "")),
            "_source": {**item, "tags": _generate_tags(item)},
        }
        for item in items
        if item.get("id") or item.get("external_id")
    ]
    try:
        ok, errors = await async_bulk(os_client, actions, raise_on_error=False)
        if errors:
            log.warning("OpenSearch bulk errors: %d failed", len(errors))
    except Exception as e:
        log.warning("OpenSearch bulk index failed: %s", e)


def _terms_filter(field: str, value: list[str] | str | None) -> dict | None:
    """Build a terms/term OpenSearch filter from a string or list."""
    if not value:
        return None
    vals = [v for v in (value if isinstance(value, list) else [value]) if v]
    if not vals:
        return None
    if len(vals) == 1:
        return {"term": {field: vals[0]}}
    return {"terms": {field: vals}}


def _to_list(value: list[str] | str | None) -> list[str] | None:
    """Normalise filter value to a list, or None if empty."""
    if not value:
        return None
    vals = [v for v in (value if isinstance(value, list) else [value]) if v]
    return vals or None


def _apply_metadata_filters(
    items: list[dict],
    os_filter: list[str] | None,
    location: list[str] | None,
    ve: list[str] | None,
    criticality: list[str] | None,
    hostgroup: list[str] | None = None,
) -> list[dict]:
    """Post-process: filter items by metadata fields after OpenSearch query.

    Applies the CheckMK filter criteria to ALL sources as a single source of truth.
    Items without the metadata field are always included (unknown = not excluded).
    """
    if not any([os_filter, location, ve, criticality, hostgroup]):
        return items

    result = []
    for item in items:
        meta = item.get("metadata") or {}
        # os/location/ve/criticality/hostgroup are CheckMK concepts.
        # For non-CheckMK sources (Graylog, Wazuh) these fields either don't exist
        # or carry a different meaning (e.g. Wazuh "location" = log path, not a site).
        # Apply these filters only to CheckMK items; other sources always pass.
        is_checkmk = item.get("source") == "checkmk"

        if is_checkmk:
            if os_filter:
                v = meta.get("os", "")
                if v and v not in os_filter:
                    continue

            if location:
                v = meta.get("location", "")
                if v and v not in location:
                    continue

            if ve:
                v = meta.get("ve", "")
                if v and v not in ve:
                    continue

            if criticality:
                v = meta.get("criticality", "")
                if v and v not in criticality:
                    continue

            if hostgroup:
                hgs = meta.get("hostgroups") or []
                if hgs and not any(hg in hostgroup for hg in hgs):
                    continue

        result.append(item)
    return result


async def get_exclusion_matchers(db: Any) -> list[dict]:
    """Build safe text matchers from active exclusion FeedSearches.

    Used by code paths that filter in Python (e.g. the AI agent's collect_data,
    which reads the Alert DB, not OpenSearch). Each matcher is
    {"terms": [lowercase phrases], "mode": "and"|"or"} derived ONLY from the
    body:/title: clauses of the query — structural fields (source/status/severity)
    are ignored so generic values like "wazuh"/"new" never cause over-filtering.

    AND-queries match only when ALL their text terms are present; OR-queries
    match when ANY is present. Mirrors the boolean intent of the exclusion.
    """
    import re
    matchers: list[dict] = []
    try:
        from sqlalchemy import select
        from app.models.workflow import FeedSearch
        result = await db.execute(
            select(FeedSearch).where(
                FeedSearch.is_exclusion == True,  # noqa: E712
                FeedSearch.enabled == True,  # noqa: E712
                FeedSearch.query_string != "",
            )
        )
        for s in result.scalars().all():
            q = s.query_string or ""
            is_or = " OR " in q.upper()
            terms: list[str] = []
            for token in re.split(r"\s+(?:OR|AND|NOT)\s+", q):
                token = token.strip().strip("()").strip()
                if ":" not in token:
                    continue
                field, val = token.split(":", 1)
                field = field.strip().lower().lstrip("(")
                if field not in ("body", "title"):
                    continue  # ignore source/status/severity/metadata.* etc.
                val = val.strip().strip('()').strip('"').rstrip("*").strip()
                if len(val) >= 3:
                    terms.append(val.lower())
            if terms:
                matchers.append({"terms": terms, "mode": "or" if is_or else "and"})
    except Exception as e:
        log.warning("Failed to build exclusion matchers: %s", e)
    return matchers


def matches_exclusion(text: str, matchers: list[dict]) -> bool:
    """True if `text` (title+body) is covered by any exclusion matcher."""
    t = (text or "").lower()
    for m in matchers:
        terms = m.get("terms") or []
        if not terms:
            continue
        if m.get("mode") == "or":
            if any(term in t for term in terms):
                return True
        else:
            if all(term in t for term in terms):
                return True
    return False


async def get_exclusion_must_not_clauses(db: Any) -> list[dict]:
    """Return OpenSearch must_not clauses for all active exclusion FeedSearches."""
    try:
        from sqlalchemy import select
        from app.models.workflow import FeedSearch
        result = await db.execute(
            select(FeedSearch).where(
                FeedSearch.is_exclusion == True,  # noqa: E712
                FeedSearch.enabled == True,  # noqa: E712
                FeedSearch.query_string != "",
            )
        )
        searches = result.scalars().all()
        clauses = []
        for s in searches:
            clauses.append({
                "query_string": {
                    "query": s.query_string,
                    "default_operator": "AND",
                    "lenient": True,
                }
            })
        return clauses
    except Exception as e:
        log.warning("Failed to load exclusion searches: %s", e)
        return []


async def count_query_matches(query_string: str, index_pattern: str) -> int:
    """Return the number of OpenSearch documents matching a query_string."""
    os = get_opensearch()
    try:
        resp = await os.count(
            index=index_pattern,
            body={"query": {"query_string": {"query": query_string, "lenient": True}}},
        )
        return int(resp.get("count", 0))
    except Exception:
        return 0


async def search(
    sources: list[str] | None = None,
    severity: str | None = None,
    host: str | None = None,
    os_filter: list[str] | str | None = None,
    location: list[str] | str | None = None,
    criticality: list[str] | str | None = None,
    ve: list[str] | str | None = None,
    hostgroup: list[str] | str | None = None,
    status: str | None = None,
    exclude_resolved: bool = False,
    user_id: str | None = None,
    checkmk_cutoff: datetime | None = None,
    from_: int = 0,
    size: int = 50,
    db: Any = None,
) -> list[dict]:
    """Search feed items across relevant indices.

    user_id: when provided, personal sources (o365/teams) are filtered to
             items owned by this user. Shared sources (checkmk/graylog/wazuh)
             are always returned regardless of user_id.
    db: when provided, active exclusion FeedSearches are applied as must_not.
    """
    indices = [_index(s) for s in (sources or ALL_SOURCES)]
    os_client = get_opensearch()

    must: list[dict] = []
    filter_: list[dict] = []
    must_not: list[dict] = []

    # Apply active exclusion FeedSearches (hide matching items)
    if db is not None:
        exclusion_clauses = await get_exclusion_must_not_clauses(db)
        must_not.extend(exclusion_clauses)

    if severity:
        filter_.append({"term": {"severity": severity}})
    if status:
        filter_.append({"term": {"status": status}})
    if exclude_resolved:
        must_not.append({"term": {"status": "resolved"}})

    if host:
        safe = host.lower().replace('"', '').replace("'", "")
        must.append({
            "bool": {
                "should": [
                    {"wildcard": {"title": {"value": f"*{safe}*", "case_insensitive": True}}},
                    {"wildcard": {"metadata.host.keyword": {"value": f"*{safe}*", "case_insensitive": True}}},
                    {"wildcard": {"metadata.agent.keyword": {"value": f"*{safe}*", "case_insensitive": True}}},
                    {"wildcard": {"metadata.source_host.keyword": {"value": f"*{safe}*", "case_insensitive": True}}},
                ],
                "minimum_should_match": 1,
            }
        })

    # CheckMK min-age: exclude items newer than cutoff
    if checkmk_cutoff:
        filter_.append({
            "bool": {
                "should": [
                    {"bool": {"must_not": [{"term": {"source": "checkmk"}}]}},
                    {"range": {"created_at": {"lte": checkmk_cutoff.isoformat()}}},
                ],
                "minimum_should_match": 1,
            }
        })

    # Per-user access control for personal sources:
    # personal items (o365, teams) must belong to this user OR be a shared source
    if user_id:
        filter_.append({
            "bool": {
                "should": [
                    # shared monitoring sources — no user restriction
                    {"terms": {"source": ["checkmk", "graylog", "wazuh", "coroot", "icinga2"]}},
                    # personal sources must match user_id
                    {"bool": {"must": [
                        {"terms": {"source": ["o365", "teams"]}},
                        {"term": {"user_id": user_id}},
                    ]}},
                ],
                "minimum_should_match": 1,
            }
        })

    query: dict[str, Any] = {"bool": {}}
    if must:
        query["bool"]["must"] = must
    if filter_:
        query["bool"]["filter"] = filter_
    if must_not:
        query["bool"]["must_not"] = must_not
    if not must and not filter_ and not must_not:
        query = {"match_all": {}}

    # Fetch more from OpenSearch when metadata filters are active so post-processing
    # can discard non-matching items and still return up to `size` results.
    needs_post_filter = any([os_filter, location, ve, criticality, hostgroup])
    fetch_size = min(size * 4, 200) if needs_post_filter else size

    body = {
        "query": query,
        "sort": [{"created_at": {"order": "desc"}}],
        "from": from_,
        "size": fetch_size,
    }

    try:
        resp = await os_client.search(index=",".join(indices), body=body, ignore_unavailable=True)
        raw = [hit["_source"] for hit in resp["hits"]["hits"]]
    except Exception as e:
        log.warning("OpenSearch search failed: %s", e)
        return []

    if needs_post_filter:
        raw = _apply_metadata_filters(
            raw,
            _to_list(os_filter),
            _to_list(location),
            _to_list(ve),
            _to_list(criticality),
            _to_list(hostgroup),
        )
        return raw[:size]

    return raw


async def get_filter_values(source: str = "checkmk") -> dict:
    """Return distinct metadata field values for filter dropdowns."""
    os_client = get_opensearch()
    body = {
        "size": 0,
        "aggs": {
            "os":          {"terms": {"field": "metadata.os.keyword",          "size": 50}},
            "location":    {"terms": {"field": "metadata.location.keyword",    "size": 100}},
            "criticality": {"terms": {"field": "metadata.criticality.keyword", "size": 20}},
            "ve":          {"terms": {"field": "metadata.ve.keyword",          "size": 20}},
            "hostgroups":  {"terms": {"field": "metadata.hostgroups.keyword",  "size": 100}},
        },
    }
    try:
        resp = await os_client.search(
            index=_index(source), body=body, ignore_unavailable=True
        )
        aggs = resp.get("aggregations", {})

        def _buckets(key: str) -> list[str]:
            return [
                b["key"]
                for b in aggs.get(key, {}).get("buckets", [])
                if b["key"]
            ]

        return {
            "os":          _buckets("os"),
            "location":    _buckets("location"),
            "criticality": _buckets("criticality"),
            "ve":          _buckets("ve"),
            "hostgroups":  _buckets("hostgroups"),
        }
    except Exception as e:
        log.warning("OpenSearch aggregation failed: %s", e)
        return {"os": [], "location": [], "criticality": [], "ve": [], "hostgroups": []}


async def delete_old_items(source: str, retention_days: int) -> int:
    """Delete items older than retention_days for the given source. Returns deleted count."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    os_client = get_opensearch()
    body = {
        "query": {
            "range": {"created_at": {"lt": cutoff.isoformat()}}
        }
    }
    try:
        resp = await os_client.delete_by_query(
            index=_index(source),
            body=body,
            ignore_unavailable=True,
            refresh=True,
        )
        deleted = resp.get("deleted", 0)
        if deleted:
            log.info("Feed housekeeping: deleted %d items from %s (>%d days)", deleted, source, retention_days)
        return deleted
    except Exception as e:
        log.warning("OpenSearch delete_old failed for %s: %s", source, e)
        return 0


async def delete_old_alerts_pg(source: str, retention_days: int, db: Any) -> int:
    """Delete alerts older than retention_days for the given source from PostgreSQL."""
    if retention_days <= 0:
        return 0
    from sqlalchemy import delete as sa_delete
    from app.models.alert import Alert
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    try:
        result = await db.execute(
            sa_delete(Alert).where(Alert.source == source, Alert.created_at < cutoff)
        )
        await db.commit()
        deleted = result.rowcount
        if deleted:
            log.info("Feed housekeeping PG: deleted %d alerts from %s (>%d days)", deleted, source, retention_days)
        return deleted
    except Exception as e:
        log.warning("PostgreSQL alert cleanup failed for %s: %s", source, e)
        return 0


async def get_hosts_metadata(hostnames: list[str]) -> dict[str, dict]:
    """Fetch the latest CheckMK metadata for a list of hostnames from OpenSearch.

    Returns {hostname: metadata_dict} for hosts found in cs-feed-checkmk.
    Used as a persistent fallback when the in-memory host cache is cold (after restart).
    """
    if not hostnames:
        return {}
    os_client = get_opensearch()
    try:
        resp = await os_client.search(
            index=_index("checkmk"),
            body={
                "query": {"terms": {"metadata.host.keyword": hostnames}},
                "sort": [{"created_at": {"order": "desc"}}],
                "size": min(len(hostnames) * 5, 500),
                "_source": ["metadata"],
            },
            ignore_unavailable=True,
        )
        result: dict[str, dict] = {}
        for hit in resp["hits"]["hits"]:
            meta = hit["_source"].get("metadata") or {}
            host = meta.get("host", "")
            if host and host not in result:  # first hit = most recent
                result[host] = meta
        return result
    except Exception as e:
        log.warning("OpenSearch host metadata lookup failed: %s", e)
        return {}


def _normalise_query_string(q: str) -> str:
    """Rewrite common AI-generated field names to the actual mapping.

    The AI often emits ``host:`` or ``metadata.host:`` for Wazuh items
    where the actual field is ``metadata.agent``. We expand those references
    so queries work across both Graylog (metadata.host) and Wazuh (metadata.agent).
    """
    import re

    # Replace bare `host:VALUE` (not already prefixed with `metadata.`) with a
    # multi-field OR so it matches Graylog and Wazuh documents alike.
    def _expand_host(m: re.Match) -> str:
        value = m.group(1)
        return f"(metadata.host:{value} OR metadata.agent:{value})"

    q = re.sub(r"(?<![\w.])host:(\S+)", _expand_host, q)
    return q


async def search_by_query(
    index_pattern: str,
    query_string: str,
    size: int = 50,
    from_: int = 0,
    user_id: str | None = None,
    host_scope: list[str] | None = None,
    db: Any = None,
) -> list[dict]:
    """Execute an OpenSearch Lucene query string against an index pattern."""
    os_client = get_opensearch()
    if query_string:
        query: dict = {"query_string": {"query": _normalise_query_string(query_string), "default_operator": "AND"}}
    else:
        query = {"match_all": {}}

    filter_clauses: list[dict] = []
    must_not: list[dict] = []
    if db is not None:
        must_not.extend(await get_exclusion_must_not_clauses(db))
    if user_id:
        filter_clauses.append({
            "bool": {
                "should": [
                    {"terms": {"source": ["checkmk", "graylog", "wazuh", "icinga2", "coroot"]}},
                    {"bool": {"must": [
                        {"terms": {"source": ["o365", "teams"]}},
                        {"term": {"user_id": user_id}},
                    ]}},
                ],
                "minimum_should_match": 1,
            }
        })

    if host_scope:
        hosts = [h for h in host_scope if h]
        if hosts:
            filter_clauses.append({
                "bool": {
                    "should": [
                        {"terms": {"metadata.host.keyword": hosts}},
                        {"terms": {"metadata.agent.keyword": hosts}},
                        {"terms": {"metadata.host_candidates.keyword": hosts}},
                    ],
                    "minimum_should_match": 1,
                }
            })

    if filter_clauses or must_not:
        body_query: dict = {"bool": {"must": [query]}}
        if filter_clauses:
            body_query["bool"]["filter"] = filter_clauses
        if must_not:
            body_query["bool"]["must_not"] = must_not
    else:
        body_query = query

    body = {
        "query": body_query,
        "sort": [{"created_at": {"order": "desc"}}],
        "from": from_,
        "size": size,
    }
    try:
        resp = await os_client.search(index=index_pattern, body=body, ignore_unavailable=True)
        return [hit["_source"] for hit in resp["hits"]["hits"]]
    except Exception as e:
        log.warning("OpenSearch search_by_query failed (%s): %s", index_pattern, e)
        return []


async def get_user_checkmk_host_scope(db, user_id: str) -> list[str]:
    """Return CheckMK hosts selected by the user's CheckMK filters.

    Empty result means no CheckMK preselection is active. When filters are set,
    Graylog/Wazuh Lucene searches are scoped to these monitored host names.
    """
    from sqlalchemy import select
    from app.models.workflow import UserPreference

    try:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
        prefs = result.scalar_one_or_none()
    except Exception as e:
        log.warning("Could not load user CheckMK scope: %s", e)
        return []

    if not prefs:
        return []

    os_filter = _to_list(prefs.checkmk_os)
    location = _to_list(prefs.checkmk_locations)
    ve = _to_list(prefs.checkmk_ve)
    criticality = _to_list(prefs.checkmk_criticality)
    if not any([os_filter, location, ve, criticality]):
        return []

    os_client = get_opensearch()
    try:
        resp = await os_client.search(
            index=_index("checkmk"),
            body={
                "query": {"match_all": {}},
                "_source": ["source", "metadata"],
                "sort": [{"created_at": {"order": "desc"}}],
                "size": 5000,
            },
            ignore_unavailable=True,
        )
        raw = [hit["_source"] for hit in resp["hits"]["hits"]]
        filtered = _apply_metadata_filters(raw, os_filter, location, ve, criticality)
        hosts: list[str] = []
        for item in filtered:
            host = ((item.get("metadata") or {}).get("host") or "").strip()
            if host and host not in hosts:
                hosts.append(host)
        return hosts
    except Exception as e:
        log.warning("Could not build CheckMK host scope: %s", e)
        return []


async def count_since(
    index_patterns: list[str],
    since: datetime,
    user_id: str | None = None,
) -> int:
    """Count feed items newer than `since`, excluding resolved ones."""
    os_client = get_opensearch()
    filter_: list[dict] = [
        {"range": {"created_at": {"gt": since.isoformat()}}},
    ]
    must_not: list[dict] = [{"term": {"status": "resolved"}}]

    if user_id:
        filter_.append({
            "bool": {
                "should": [
                    {"terms": {"source": ["checkmk", "graylog", "wazuh", "icinga2", "coroot"]}},
                    {"bool": {"must": [
                        {"terms": {"source": ["o365", "teams"]}},
                        {"term": {"user_id": user_id}},
                    ]}},
                ],
                "minimum_should_match": 1,
            }
        })

    body = {
        "query": {"bool": {"filter": filter_, "must_not": must_not}},
        "size": 0,
        "track_total_hits": True,
    }
    indices = ",".join(index_patterns)
    try:
        resp = await os_client.search(index=indices, body=body, ignore_unavailable=True)
        total = resp.get("hits", {}).get("total", {})
        return total.get("value", 0) if isinstance(total, dict) else int(total)
    except Exception as e:
        log.warning("OpenSearch count_since failed: %s", e)
        return 0


async def get_by_id(doc_id: str) -> dict | None:
    """Fetch a single feed item by its document ID, searching all cs-feed-* indices."""
    os_client = get_opensearch()
    indices = ",".join([_index(s) for s in ALL_SOURCES])
    try:
        resp = await os_client.search(
            index=indices,
            body={"query": {"term": {"id": doc_id}}, "size": 1},
            ignore_unavailable=True,
        )
        hits = resp["hits"]["hits"]
        if hits:
            return hits[0]["_source"]
        return None
    except Exception as e:
        log.warning("OpenSearch get_by_id failed for %s: %s", doc_id, e)
        return None


async def update_status(doc_id: str, source: str, status: str) -> None:
    """Update the status field of a feed item (e.g. acknowledged)."""
    os_client = get_opensearch()
    try:
        await os_client.update(
            index=_index(source),
            id=str(doc_id),
            body={"doc": {"status": status}},
            ignore=404,
        )
    except Exception as e:
        log.warning("OpenSearch update_status failed: %s", e)


async def update_ai_resolution(external_id: str, summary: str) -> None:
    """Tag an alert in OpenSearch as AI-resolved and store the resolution text.

    Called by the computer-resolve endpoint after Hermes marks a problem solved.
    Sets has_ai_resolution=True, ai_resolution_text, ai_insight, and appends
    the "ai_resolved" tag to the existing tags array.
    """
    os_client = get_opensearch()
    try:
        resp = await os_client.search(
            index=f"{INDEX_PREFIX}-*",
            body={"query": {"term": {"external_id": external_id}}, "size": 1},
        )
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            log.warning("update_ai_resolution: no doc found for external_id=%s", external_id)
            return
        hit = hits[0]
        # Use a Painless script to append "ai_resolved" to the tags array without duplicating
        await os_client.update(
            index=hit["_index"],
            id=hit["_id"],
            body={
                "script": {
                    "lang": "painless",
                    "source": (
                        "if (ctx._source.tags == null) { ctx._source.tags = new ArrayList(); } "
                        "if (!ctx._source.tags.contains('ai_resolved')) { ctx._source.tags.add('ai_resolved'); } "
                        "ctx._source.has_ai_resolution = true; "
                        "ctx._source.ai_resolution_text = params.summary; "
                        "ctx._source.ai_insight = params.summary;"
                    ),
                    "params": {"summary": summary[:1000]},
                }
            },
        )
        log.info("update_ai_resolution: tagged %s in %s", external_id, hit["_index"])
    except Exception as e:
        log.warning("update_ai_resolution failed for %s: %s", external_id, e)


async def search_ai_resolved(
    alert_title: str | None = None,
    host: str | None = None,
    limit: int = 3,
) -> list[dict]:
    """Search for past AI-resolved alerts similar to the current problem.

    Strategy:
    - If alert_title is given (≥10 chars): more_like_this on title field to find
      semantically similar solved problems across all hosts.
    - Otherwise: filter by host to find any past solved problem on the same machine.

    Returns list of dicts with keys: source, run_at, severity, finding_title, resolution.
    """
    if not alert_title and not host:
        return []

    os_client = get_opensearch()

    if alert_title and len(alert_title) >= 10:
        query: dict = {
            "bool": {
                "must": [
                    {
                        "more_like_this": {
                            "fields": ["title", "ai_resolution_text"],
                            "like": alert_title,
                            "min_term_freq": 1,
                            "max_query_terms": 12,
                            "min_doc_freq": 1,
                            "minimum_should_match": "25%",
                        }
                    }
                ],
                "filter": [{"term": {"has_ai_resolution": True}}],
            }
        }
    else:
        # Fallback: any AI-resolved alert on the same host
        host_filters: list[dict] = []
        if host:
            host_filters = [
                {"term": {"metadata.host.keyword": host}},
                {"term": {"metadata.agent.keyword": host}},
            ]
        query = {
            "bool": {
                "must": [{"term": {"has_ai_resolution": True}}],
                "should": host_filters,
                "minimum_should_match": 1 if host_filters else 0,
            }
        }

    try:
        resp = await os_client.search(
            index=f"{INDEX_PREFIX}-*",
            body={
                "query": query,
                "sort": [{"created_at": {"order": "desc"}}],
                "size": limit,
                "_source": ["title", "ai_resolution_text", "severity", "created_at", "external_id", "metadata"],
            },
        )
        results = []
        for hit in resp.get("hits", {}).get("hits", []):
            src = hit["_source"]
            results.append({
                "source": "ai_resolution",
                "run_at": (src.get("created_at") or "")[:19],
                "severity": src.get("severity", "?"),
                "finding_title": (src.get("title") or "")[:120],
                "recommendation": "",
                "resolution": (src.get("ai_resolution_text") or "")[:400],
            })
        return results
    except Exception as e:
        log.warning("search_ai_resolved failed: %s", e)
        return []


async def search_recent_ai_findings(host: str, limit: int = 3) -> list[dict]:
    """Return recent sysadmin AI findings that match the given host.

    Queries the last 3 AiAnalysis runs (within 4 hours) from PostgreSQL,
    filters findings where finding["host"] is a substring of `host` or vice versa.
    Returns up to `limit` dicts with keys: severity, title, description.
    """
    from datetime import timedelta
    from sqlalchemy import select, and_
    from app.core.database import AsyncSessionLocal
    from app.models.ai import AiAnalysis

    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AiAnalysis)
                .where(
                    and_(
                        AiAnalysis.agent_type == "sysadmin",
                        AiAnalysis.run_at >= cutoff,
                    )
                )
                .order_by(AiAnalysis.run_at.desc())
                .limit(3)
            )
            analyses = result.scalars().all()

        host_lower = host.lower()
        findings: list[dict] = []
        for analysis in analyses:
            for f in (analysis.findings or []):
                f_host = (f.get("host") or "").lower()
                if f_host and (host_lower in f_host or f_host in host_lower):
                    findings.append({
                        "severity": f.get("severity", "?"),
                        "title": f.get("title", ""),
                        "description": f.get("description", ""),
                    })
                    if len(findings) >= limit:
                        return findings
        return findings
    except Exception as e:
        log.warning("search_recent_ai_findings failed: %s", e)
        return []
