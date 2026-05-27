"""News Feed — unified event stream stored in OpenSearch.

Primary storage: OpenSearch (cs-feed-{source} indices).
Live sources: O365 mail and Teams messages are fetched on-demand and
              also indexed in OpenSearch for persistence + history.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.workflow import FeedSearch, UserPreference

router = APIRouter(prefix="/feed", tags=["feed"])

_ALL_SOURCES = ["checkmk", "graylog", "wazuh", "o365", "teams"]


async def _get_prefs(user_id, db: AsyncSession) -> UserPreference | None:
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def _get_preferred_connector(
    db: AsyncSession,
    connector_type: str,
    user_id,
):
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.type == connector_type,
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
        .order_by(ConnectorConfig.owner_user_id.is_(None), ConnectorConfig.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _fetch_and_index_o365(prefs: UserPreference, db: AsyncSession, user_id: str) -> list[dict]:
    """Fetch unread O365 mails, index them, return as feed items."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.o365 import O365Connector
    from app.services.feed_index import index_items

    connector = await _get_preferred_connector(db, "o365", prefs.user_id)
    if not connector:
        return []

    creds = decrypt_credentials(connector.encrypted_credentials)
    o365 = O365Connector(connector.base_url, creds)
    mails = await o365.get_unread_mails(
        prefs.o365_mailbox, prefs.o365_folder or "Inbox", top=20
    )
    items = []
    for mail in mails:
        sender = (
            mail.get("from", {}).get("emailAddress", {}).get("address", "")
        )
        item = {
            "id": f"mail_{mail.get('id', '')}",
            "type": "email",
            "source": "o365",
            "severity": "info",
            "title": mail.get("subject") or "(kein Betreff)",
            "body": mail.get("bodyPreview", ""),
            "metadata": {"from": sender, "received_at": mail.get("receivedDateTime", "")},
            "created_at": mail.get("receivedDateTime", ""),
            "status": "new",
            "location_name": None,
            "location_city": None,
            "external_url": mail.get("webLink"),
            "user_id": user_id,
        }
        items.append(item)
    # Index to OpenSearch (idempotent — same id = upsert)
    await index_items(items)
    return items


async def _fetch_and_index_teams(
    prefs: UserPreference, db: AsyncSession, user_id: str
) -> list[dict]:
    """Fetch Teams channel messages, index them, return as feed items."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.teams import TeamsConnector
    from app.services.feed_index import index_items

    channels = prefs.feed_teams_channels or []
    if not channels:
        return []

    connector = await _get_preferred_connector(db, "teams", prefs.user_id)
    if not connector:
        return []

    creds = decrypt_credentials(connector.encrypted_credentials)
    tc = TeamsConnector(connector.base_url, creds)
    items = []
    for channel_id in channels[:5]:
        msgs = await tc.get_channel_messages(channel_id, top=10)
        for msg in msgs:
            item = {
                "id": f"teams_{msg.get('id', '')}",
                "type": "teams_message",
                "source": "teams",
                "severity": "info",
                "title": msg.get("channelName", "Teams"),
                "body": msg.get("body", {}).get("content", ""),
                "metadata": {
                    "from": msg.get("from", {}).get("user", {}).get("displayName", ""),
                    "channel_id": channel_id,
                },
                "created_at": msg.get("createdDateTime", ""),
                "status": "new",
                "location_name": None,
                "location_city": None,
                "external_url": msg.get("webUrl"),
                "user_id": user_id,
            }
            items.append(item)
    await index_items(items)
    return items


@router.get("/")
async def get_feed(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    sources: str | None = Query(None, description="Comma-separated source filter"),
    severity: str | None = Query(None),
    host: str | None = Query(None),
    os: str | None = Query(None),
    location: str | None = Query(None),
    criticality: str | None = Query(None),
    ve: str | None = Query(None),
    hostgroup: str | None = Query(None, description="Comma-separated hostgroup filter"),
    search_id: _uuid.UUID | None = Query(None),
    index: str | None = Query(None, description="OpenSearch index pattern for direct query mode"),
    q: str | None = Query(None, description="OpenSearch Lucene query string for direct query mode"),
    highlight_id: str | None = Query(None, description="Fetch this item by ID and pin it at the top"),
):
    """Return unified news feed from OpenSearch, sorted by created_at desc."""
    from app.services import feed_index

    prefs = await _get_prefs(user.id, db)
    if search_id or q is not None:
        index_pattern = index or "cs-feed-*"
        query_string = q or ""
        if search_id:
            result = await db.execute(select(FeedSearch).where(FeedSearch.id == search_id))
            saved = result.scalar_one_or_none()
            if not saved:
                raise HTTPException(404, "Search not found")
            if saved.user_id and saved.user_id != user.id:
                raise HTTPException(403, "Not your search")
            index_pattern = saved.index_pattern
            query_string = saved.query_string
        return await feed_index.search_by_query(
            index_pattern=index_pattern,
            query_string=query_string,
            user_id=str(user.id),
            host_scope=await feed_index.get_user_checkmk_host_scope(db, str(user.id)),
            from_=offset,
            size=limit,
        )

    min_age = (prefs.feed_checkmk_min_age_minutes if prefs else None) or 5
    enabled = (prefs.feed_sources_enabled if prefs else None) or ["checkmk", "graylog", "wazuh"]

    requested = sources.split(",") if sources else enabled
    active = [s.strip() for s in requested if s.strip() in _ALL_SOURCES]

    # Refresh live sources on every request (idempotent — re-indexes)
    uid_str = str(user.id)

    if "o365" in active and prefs and prefs.o365_mailbox:
        try:
            await _fetch_and_index_o365(prefs, db, uid_str)
        except Exception:
            pass

    if "teams" in active and prefs and (prefs.feed_teams_channels or []):
        try:
            await _fetch_and_index_teams(prefs, db, uid_str)
        except Exception:
            pass

    # For CheckMK: exclude items newer than min_age from search
    # We handle this via a date range filter passed to search
    checkmk_cutoff: datetime | None = None
    if "checkmk" in active:
        checkmk_cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age)

    # Apply user's saved CheckMK preferences as default filters when no explicit
    # filter param is provided in the request.
    def _pref_list(val: list | None) -> list[str] | None:
        return [str(v) for v in val if v] if val else None

    effective_os          = os          or (_pref_list(prefs.checkmk_os)          if prefs else None)
    effective_location    = location    or (_pref_list(prefs.checkmk_locations)   if prefs else None)
    effective_ve          = ve          or (_pref_list(prefs.checkmk_ve)          if prefs else None)
    effective_criticality = criticality or (_pref_list(prefs.checkmk_criticality) if prefs else None)
    effective_hostgroup   = [v.strip() for v in hostgroup.split(",") if v.strip()] if hostgroup else None

    items = await feed_index.search(
        sources=active,
        severity=severity,
        host=host,
        os_filter=effective_os,
        location=effective_location,
        criticality=effective_criticality,
        ve=effective_ve,
        hostgroup=effective_hostgroup,
        exclude_resolved=True,
        checkmk_cutoff=checkmk_cutoff,
        user_id=str(user.id),
        from_=offset,
        size=limit,
        db=db,
    )

    if highlight_id:
        # Ensure the highlighted item is in the result set (it may be older than current page)
        already_present = any(i.get("id") == highlight_id for i in items)
        if not already_present:
            pinned = await feed_index.get_by_id(highlight_id)
            if pinned:
                items = [pinned] + list(items)

    return items


@router.get("/checkmk-filter-values")
async def get_checkmk_filter_values(user: CurrentUser):
    """Return distinct OS/location/criticality/VE values from OpenSearch aggregations."""
    from app.services.feed_index import get_filter_values
    return await get_filter_values("checkmk")


@router.get("/unread-count")
async def get_unread_count(
    user: CurrentUser,
    since: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Count feed items newer than `since` (ISO timestamp) for the current user."""
    from datetime import datetime, timezone
    from app.services.feed_index import count_since, ALL_SOURCES, _index

    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        since_dt = datetime.fromtimestamp(0, tz=timezone.utc)

    count = await count_since(
        index_patterns=[f"cs-feed-{s}" for s in ALL_SOURCES],
        since=since_dt,
        user_id=str(user.id),
    )
    return {"count": count}


@router.post("/{item_id}/enrich")
async def enrich_feed_item(
    item_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """On-demand KI enrichment for a single feed item. Returns ai_insight text."""
    from app.services import feed_index
    from app.services.feed_enricher import enrich_single
    from app.services.settings import get_llm_config

    item = await feed_index.get_by_id(item_id)
    if not item:
        raise HTTPException(404, "Feed-Item nicht gefunden")

    # Return cached insight if already enriched
    if item.get("ai_insight"):
        return {"ai_insight": item["ai_insight"]}

    llm_config = await get_llm_config(db)
    if not llm_config.is_configured:
        raise HTTPException(503, "LLM nicht konfiguriert")

    insight = await enrich_single(item, llm_config)
    if not insight:
        raise HTTPException(500, "KI-Anreicherung fehlgeschlagen")

    return {"ai_insight": insight}


@router.post("/{alert_id}/acknowledge")
async def acknowledge_feed_item(
    alert_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Acknowledge a monitoring alert — updates PostgreSQL and OpenSearch."""
    from app.services.feed_index import update_status

    # Try to parse as UUID (DB alert)
    try:
        uid = _uuid.UUID(alert_id)
        result = await db.execute(select(Alert).where(Alert.id == uid))
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(404, "Alert not found")
        alert.status = "acknowledged"
        alert.acknowledged_by = user.id
        db.add(
            AuditLog(
                action="alert_acknowledged",
                resource_type="alert",
                resource_id=str(uid),
                user_id=user.id,
            )
        )
        await db.commit()
        # Mirror status to OpenSearch
        source = alert.source
    except (ValueError, HTTPException):
        # Non-UUID id (email/teams) — only update OpenSearch
        source = alert_id.split("_")[0] if "_" in alert_id else "unknown"
        uid = None

    await update_status(alert_id, source, "acknowledged")
    return {"ok": True}
