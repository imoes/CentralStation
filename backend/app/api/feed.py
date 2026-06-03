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

    # When an explicit host filter is given (e.g. from bridge click), skip the
    # preference-based default filters — the user wants to see THAT host regardless
    # of their saved location/ve/criticality scope.
    pref_override = bool(host)
    effective_os          = os          or (None if pref_override else _pref_list(prefs.checkmk_os)          if prefs else None)
    effective_location    = location    or (None if pref_override else _pref_list(prefs.checkmk_locations)   if prefs else None)
    effective_ve          = ve          or (None if pref_override else _pref_list(prefs.checkmk_ve)          if prefs else None)
    effective_criticality = criticality or (None if pref_override else _pref_list(prefs.checkmk_criticality) if prefs else None)
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

    # ── Enrich with collaboration state ──────────────────────────────────────
    # Batch-load collab rows + comment counts for all external_ids in this page.
    ext_ids = [i.get("external_id") for i in items if i.get("external_id")]
    if ext_ids:
        try:
            from app.models.workflow import AlertCollaboration, AlertComment
            from sqlalchemy import func as sa_func
            collab_rows = await db.execute(
                select(AlertCollaboration).where(AlertCollaboration.external_id.in_(ext_ids))
            )
            collab_map = {r.external_id: r for r in collab_rows.scalars().all()}

            count_rows = await db.execute(
                select(AlertComment.external_id, sa_func.count(AlertComment.id).label("n"))
                .where(AlertComment.external_id.in_(ext_ids))
                .group_by(AlertComment.external_id)
            )
            count_map = {r.external_id: r.n for r in count_rows.all()}

            enriched = []
            for item in items:
                eid = item.get("external_id")
                c = collab_map.get(eid) if eid else None
                item["collab"] = {
                    "claimed_by_name": c.claimed_by_name if c else None,
                    "claimed_at": c.claimed_at.isoformat() if c and c.claimed_at else None,
                    "work_status": c.work_status if c else "new",
                    "comment_count": count_map.get(eid, 0) if eid else 0,
                }
                enriched.append(item)
            items = enriched
        except Exception:
            pass  # collab enrichment is non-critical

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
    from app.services.settings import get_llm_config, get_agent_config, get_searxng_config

    item = await feed_index.get_by_id(item_id)
    if not item:
        raise HTTPException(404, "Feed-Item nicht gefunden")

    # Return cached insight if already enriched
    if item.get("ai_insight"):
        return {"ai_insight": item["ai_insight"]}

    llm_config = await get_llm_config(db)
    if not llm_config.is_configured:
        raise HTTPException(503, "LLM nicht konfiguriert")

    agent_cfg = await get_agent_config(db)
    searxng = await get_searxng_config(db)
    searxng_url = searxng.base_url if (agent_cfg.workflow_web_search and searxng.is_configured) else ""

    insight = await enrich_single(item, llm_config, searxng_url=searxng_url)
    if not insight:
        raise HTTPException(500, "KI-Anreicherung fehlgeschlagen")

    # Adaptive learning: user manually requested LLM → this pattern was relevant
    if agent_cfg.score_learning_enabled:
        try:
            from app.services.alert_score_learner import record_manual_enrich_requested
            await record_manual_enrich_requested(item, db)
        except Exception:
            pass

    return {"ai_insight": insight}


@router.post("/{item_id}/ignore")
async def ignore_feed_item(
    item_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Use AI to generate an OpenSearch exclusion query and save it as a system FeedSearch."""
    from app.services import feed_index
    from app.services.settings import get_llm_config
    from app.services.workflow_ai import generate_exclusion_query
    from app.models.workflow import FeedSearch

    item = await feed_index.get_by_id(item_id)
    if not item:
        raise HTTPException(404, "Feed-Item nicht gefunden")

    llm_config = await get_llm_config(db)
    if not llm_config.is_configured:
        raise HTTPException(503, "LLM nicht konfiguriert")

    result = await generate_exclusion_query(llm_config, item)
    query_string = result["query"]
    name = result["name"]

    source = item.get("source", "")
    index_map = {"graylog": "cs-feed-graylog", "wazuh": "cs-feed-wazuh", "checkmk": "cs-feed-checkmk"}
    index_pattern = index_map.get(source, "cs-feed-*")

    search = FeedSearch(
        user_id=None,
        index_pattern=index_pattern,
        name=name,
        query_string=query_string,
        enabled=True,
        is_system=True,
        is_exclusion=True,
        position=97,
    )
    db.add(search)
    await db.commit()
    await db.refresh(search)

    return {"id": str(search.id), "name": name, "query_string": query_string}


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


# ── Collaboration: claim / comments / status ──────────────────────────────

async def _get_or_create_collab(
    external_id: str, db: AsyncSession
) -> "AlertCollaboration":
    from app.models.workflow import AlertCollaboration
    result = await db.execute(
        select(AlertCollaboration).where(AlertCollaboration.external_id == external_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        row = AlertCollaboration(external_id=external_id)
        db.add(row)
        await db.flush()
    return row


async def _add_timeline(
    external_id: str, user_id, user_name: str,
    kind: str, body: str, db: AsyncSession
) -> dict:
    from app.models.workflow import AlertComment
    import uuid as _uuid2
    entry = AlertComment(
        id=_uuid2.uuid4(),
        external_id=external_id,
        user_id=user_id,
        user_name=user_name,
        kind=kind,
        body=body,
    )
    db.add(entry)
    return {"id": str(entry.id), "user_name": user_name, "kind": kind, "body": body}


async def _broadcast_collab(external_id: str, kind: str, user_name: str,
                             work_status: str, body: str) -> None:
    from app.api.ws import manager
    try:
        await manager.broadcast(
            {
                "type": "feed_collab",
                "external_id": external_id,
                "kind": kind,
                "user_name": user_name,
                "work_status": work_status,
                "body": body,
            },
            roles=["admin", "sysadmin", "network_technician"],
        )
    except Exception:
        pass


@router.post("/{external_id}/claim")
async def claim_alert(
    external_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Claim ownership of an alert — 'I'm working on this'."""
    from datetime import datetime, timezone
    collab = await _get_or_create_collab(external_id, db)
    if collab.claimed_by and collab.claimed_by != user.id:
        raise HTTPException(409, f"Already claimed by {collab.claimed_by_name}")
    collab.claimed_by = user.id
    collab.claimed_by_name = user.full_name or user.email
    collab.claimed_at = datetime.now(timezone.utc)
    collab.work_status = "investigating"
    body = f"{collab.claimed_by_name} übernimmt das Problem."
    await _add_timeline(external_id, user.id, collab.claimed_by_name, "claim", body, db)
    await db.commit()
    await _broadcast_collab(external_id, "claim", collab.claimed_by_name, "investigating", body)
    return {"ok": True, "claimed_by_name": collab.claimed_by_name, "work_status": "investigating"}


@router.post("/{external_id}/release")
async def release_alert(
    external_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Release claim on an alert."""
    collab = await _get_or_create_collab(external_id, db)
    name = user.full_name or user.email
    collab.claimed_by = None
    collab.claimed_by_name = None
    collab.claimed_at = None
    collab.work_status = "new"
    body = f"{name} gibt das Problem frei."
    await _add_timeline(external_id, user.id, name, "release", body, db)
    await db.commit()
    await _broadcast_collab(external_id, "release", name, "new", body)
    return {"ok": True}


@router.patch("/{external_id}/status")
async def set_alert_status(
    external_id: str,
    body: dict,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set work_status (new|investigating|resolved)."""
    new_status = body.get("status", "new")
    if new_status not in ("new", "investigating", "resolved"):
        raise HTTPException(400, "Invalid status")
    collab = await _get_or_create_collab(external_id, db)
    collab.work_status = new_status
    name = user.full_name or user.email
    status_labels = {"new": "Neu", "investigating": "In Bearbeitung", "resolved": "Gelöst"}
    entry_body = f"Status → {status_labels.get(new_status, new_status)}"
    await _add_timeline(external_id, user.id, name, "status", entry_body, db)
    await db.commit()
    await _broadcast_collab(external_id, "status", name, new_status, entry_body)
    return {"ok": True, "work_status": new_status}


@router.post("/{external_id}/comments")
async def add_comment(
    external_id: str,
    body: dict,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add a user comment to an alert's activity timeline."""
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "Comment body required")
    name = user.full_name or user.email
    # ensure collab row exists
    collab = await _get_or_create_collab(external_id, db)
    entry = await _add_timeline(external_id, user.id, name, "comment", text, db)
    await db.commit()
    await _broadcast_collab(external_id, "comment", name, collab.work_status, text)
    return {"ok": True, "entry": entry}


@router.get("/{external_id}/collab")
async def get_collab(
    external_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get claim state + work_status + full activity timeline for an alert."""
    from app.models.workflow import AlertCollaboration, AlertComment
    from sqlalchemy import select as sa_select

    collab_result = await db.execute(
        sa_select(AlertCollaboration).where(AlertCollaboration.external_id == external_id)
    )
    collab = collab_result.scalar_one_or_none()

    comments_result = await db.execute(
        sa_select(AlertComment)
        .where(AlertComment.external_id == external_id)
        .order_by(AlertComment.created_at.asc())
    )
    comments = comments_result.scalars().all()

    return {
        "external_id": external_id,
        "claimed_by_name": collab.claimed_by_name if collab else None,
        "claimed_at": collab.claimed_at.isoformat() if collab and collab.claimed_at else None,
        "work_status": collab.work_status if collab else "new",
        "timeline": [
            {
                "id": str(c.id),
                "user_name": c.user_name,
                "kind": c.kind,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
            }
            for c in comments
        ],
    }


@router.post("/{external_id}/diagnose")
async def diagnose_alert(
    external_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run read-only diagnostics for the host behind an alert and post result as AI comment.

    Sci-Fi pattern: "Computer, prüfe das" — the AI runs checks autonomously and
    reports what it found as a completed action, not as a question.
    All checks are guaranteed read-only (no mutation, full audit).
    """
    from app.services.ai_agent.diagnostics import run_diagnostics
    from app.services.settings import get_llm_config
    from app.services.llm_client import generate_text

    # ── 1. Find the host for this external_id ─────────────────────────────
    # Look up via OpenSearch first, fall back to DB Alert
    host = ""
    try:
        from app.services.feed_index import search_by_query
        items = await search_by_query(
            index_pattern="cs-feed-*",
            query_string=f'external_id:"{external_id}"',
            size=1,
        )
        if items:
            meta = items[0].get("metadata") or {}
            host = meta.get("host") or meta.get("agent") or meta.get("container_name") or ""
            if not host:
                host = items[0].get("title", "")[:60]
    except Exception:
        pass

    if not host:
        raise HTTPException(400, "Host nicht ermittelbar — Diagnose nicht möglich.")

    # ── 2. Run diagnostic providers (all read-only) ───────────────────────
    results = await run_diagnostics(host, db)
    if not results:
        raise HTTPException(503, "Keine Diagnose-Provider verfügbar.")

    # ── 3. Let LLM synthesise a human-readable summary ───────────────────
    findings_text = "\n".join(r.to_llm_text() for r in results)
    summary = ""
    try:
        llm_cfg = await get_llm_config(db)
        if llm_cfg.is_configured:
            raw = await generate_text(
                llm_cfg,
                [
                    {"role": "system", "content":
                     "Du bist ein IT-Operations-Assistent. Fasse die Diagnoseergebnisse "
                     "für einen Sysadmin in 2-3 präzisen deutschen Sätzen zusammen. "
                     "Markiere kritische Punkte. Weise darauf hin, dass NUR gelesen wurde "
                     "(read-only, keine Änderung vorgenommen). KEINE Widget-Namen nennen."},
                    {"role": "user", "content":
                     f"Diagnoseergebnisse für {host}:\n{findings_text}"},
                ],
                temperature=0.2,
                max_output_tokens=300,
            )
            from app.services.dashboard.generative_designer import _strip_thinking
            summary = _strip_thinking(raw).strip()
    except Exception as e:
        log.debug("diagnose: LLM synthesis failed: %s", e)
        summary = findings_text  # fallback: raw results

    # ── 4. Post as AI comment in the collaboration thread ─────────────────
    collab = await _get_or_create_collab(external_id, db)
    ai_body = f"🖥 Diagnose (read-only, keine Änderung):\n{summary}"
    entry = await _add_timeline(
        external_id, None, "Computer (KI)", "ai", ai_body, db
    )
    await db.commit()
    await _broadcast_collab(external_id, "ai", "Computer (KI)", collab.work_status, ai_body)

    # Also surface in items if comment thread is open
    return {
        "ok": True,
        "host": host,
        "summary": summary,
        "providers": [r.tool for r in results],
        "entry": entry,
    }
