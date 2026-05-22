"""Feed Index Service — stores all feed items in OpenSearch.

Index naming: cs-feed-{source}  (e.g. cs-feed-checkmk, cs-feed-o365)
Retention: configurable per source via DELETE by query (daily scheduler job).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.opensearch import get_opensearch

log = logging.getLogger(__name__)

# One index per source for independent retention policies
INDEX_PREFIX = "cs-feed"
ALL_SOURCES = ["checkmk", "graylog", "wazuh", "o365", "teams"]

_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id":            {"type": "keyword"},
            "type":          {"type": "keyword"},
            "source":        {"type": "keyword"},
            "severity":      {"type": "keyword"},
            "title":         {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "body":          {"type": "text"},
            "metadata":      {"type": "object", "dynamic": True},
            "created_at":    {"type": "date"},
            "status":        {"type": "keyword"},
            "location_name": {"type": "keyword"},
            "location_city": {"type": "keyword"},
            "external_url":  {"type": "keyword"},
            "external_id":   {"type": "keyword"},
            # owner of personal items (o365, teams) — empty = shared/all-roles
            "user_id":       {"type": "keyword"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}


def _index(source: str) -> str:
    return f"{INDEX_PREFIX}-{source}"


async def ensure_indices() -> None:
    """Create indices if they don't exist. Called at startup."""
    os_client = get_opensearch()
    for source in ALL_SOURCES:
        idx = _index(source)
        try:
            exists = await os_client.indices.exists(index=idx)
            if not exists:
                await os_client.indices.create(index=idx, body=_INDEX_MAPPING)
                log.info("Created OpenSearch index: %s", idx)
        except Exception as e:
            log.warning("Could not create index %s: %s", idx, e)


async def index_item(item: dict) -> None:
    """Index a single feed item. item must have 'id' and 'source'."""
    source = item.get("source", "unknown")
    doc_id = item.get("id") or item.get("external_id")
    if not doc_id:
        return
    os_client = get_opensearch()
    try:
        await os_client.index(
            index=_index(source),
            id=str(doc_id),
            body=item,
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
            "_source": item,
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


async def search(
    sources: list[str] | None = None,
    severity: str | None = None,
    host: str | None = None,
    os_filter: str | None = None,
    location: str | None = None,
    criticality: str | None = None,
    ve: str | None = None,
    status: str | None = None,
    user_id: str | None = None,
    checkmk_cutoff: datetime | None = None,
    from_: int = 0,
    size: int = 50,
) -> list[dict]:
    """Search feed items across relevant indices.

    user_id: when provided, personal sources (o365/teams) are filtered to
             items owned by this user. Shared sources (checkmk/graylog/wazuh)
             are always returned regardless of user_id.
    """
    indices = [_index(s) for s in (sources or ALL_SOURCES)]
    os_client = get_opensearch()

    must: list[dict] = []
    filter_: list[dict] = []

    if severity:
        filter_.append({"term": {"severity": severity}})
    if status:
        filter_.append({"term": {"status": status}})

    if host:
        must.append({"match": {"title": {"query": host, "fuzziness": "AUTO"}}})
    if os_filter:
        filter_.append({"term": {"metadata.os": os_filter}})
    if location:
        filter_.append({"term": {"metadata.location": location}})
    if criticality:
        filter_.append({"term": {"metadata.criticality": criticality}})
    if ve:
        filter_.append({"term": {"metadata.ve": ve}})

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
                    {"terms": {"source": ["checkmk", "graylog", "wazuh"]}},
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
    if not must and not filter_:
        query = {"match_all": {}}

    body = {
        "query": query,
        "sort": [{"created_at": {"order": "desc"}}],
        "from": from_,
        "size": size,
    }

    try:
        resp = await os_client.search(index=",".join(indices), body=body, ignore_unavailable=True)
        return [hit["_source"] for hit in resp["hits"]["hits"]]
    except Exception as e:
        log.warning("OpenSearch search failed: %s", e)
        return []


async def get_filter_values(source: str = "checkmk") -> dict:
    """Return distinct metadata field values for filter dropdowns."""
    os_client = get_opensearch()
    body = {
        "size": 0,
        "aggs": {
            "os":          {"terms": {"field": "metadata.os",          "size": 50}},
            "location":    {"terms": {"field": "metadata.location",    "size": 100}},
            "criticality": {"terms": {"field": "metadata.criticality", "size": 20}},
            "ve":          {"terms": {"field": "metadata.ve",          "size": 20}},
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
        }
    except Exception as e:
        log.warning("OpenSearch aggregation failed: %s", e)
        return {"os": [], "location": [], "criticality": [], "ve": []}


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
