"""News Feed — unified event stream stored in OpenSearch.

Primary storage: OpenSearch (cs-feed-{source} indices).
Live sources: O365 mail and Teams messages are fetched on-demand and
              also indexed in OpenSearch for persistence + history.
"""
from __future__ import annotations

import logging
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
log = logging.getLogger(__name__)

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

            # Batch-load incident memberships
            from app.models.workflow import IncidentMember
            incident_rows = await db.execute(
                select(IncidentMember.external_id, IncidentMember.incident_id)
                .where(IncidentMember.external_id.in_(ext_ids))
            )
            incident_map: dict[str, str] = {
                str(r.external_id): str(r.incident_id)
                for r in incident_rows.all()
            }

            enriched = []
            for item in items:
                eid = item.get("external_id")
                c = collab_map.get(eid) if eid else None
                item["collab"] = {
                    "claimed_by_name": c.claimed_by_name if c else None,
                    "claimed_at": c.claimed_at.isoformat() if c and c.claimed_at else None,
                    "work_status": c.work_status if c else "new",
                    "comment_count": count_map.get(eid, 0) if eid else 0,
                    "incident_id": incident_map.get(eid) if eid else None,
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
        from app.services.settings import get_active_llm_config
        llm_cfg = await get_active_llm_config(db)
        if llm_cfg.is_configured:
            system_prompt = (
                "Du bist ein IT-Operations-Assistent. Fasse die Diagnoseergebnisse "
                "für einen Sysadmin in 2-3 präzisen deutschen Sätzen zusammen. "
                "Markiere kritische Punkte mit konkreten Werten (z.B. 'DCX_API_max: 7543ms'). "
                "BEWEISPFLICHT: Nenne NUR Probleme, die aus den Diagnosedaten direkt hervorgehen — "
                "keine Vermutungen ohne Datenbeleg. "
                "ABWESENHEIT VON DATEN IST KEIN BEFUND: Fehlende Metriken oder leere "
                "Ergebnisse bedeuten 'gesund/nicht gesammelt', NICHT 'Problem'. Werte sie "
                "niemals als auffällig — erwähne sie höchstens neutral. "
                "Weise darauf hin, dass NUR gelesen wurde (read-only, keine Änderung). "
                "Das Feld 'Log-Quelle' nennt den Kollektor (Graylog, CheckMK) — NICHT das Problem-System."
            )
            raw = await generate_text(
                llm_cfg,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Diagnoseergebnisse für {host}:\n{findings_text}"},
                ],
                temperature=0.2,
            )
            from app.services.dashboard.generative_designer import _strip_thinking
            summary = _strip_thinking(raw).strip()
    except Exception as e:
        log.warning("diagnose: LLM synthesis failed, using raw provider results: %s", e)
        summary = findings_text  # fallback: raw results

    # ── 4. Collect deterministic evidence from the providers (not the LLM) ─
    # The LLM summary can drift; the provider evidence is ground truth.
    evidence: list[dict] = []
    for r in results:
        for ev in getattr(r, "evidence", []) or []:
            evidence.append(ev)

    # ── 5. Post as AI comment with an evidence block appended ─────────────
    collab = await _get_or_create_collab(external_id, db)
    ai_body = f"🖥 Diagnose (read-only, keine Änderung):\n{summary}"
    if evidence:
        lines = ["", "📎 Belege:"]
        for ev in evidence[:8]:
            etype = ev.get("type", "?")
            ref = ev.get("ref", "")
            text = (ev.get("text", "") or "")[:120]
            lines.append(f"• [{etype}] {ref} — {text}")
        ai_body += "\n" + "\n".join(lines)
    entry = await _add_timeline(
        external_id, None, "Computer (KI)", "ai", ai_body, db
    )
    await db.commit()
    await _broadcast_collab(external_id, "ai", "Computer (KI)", collab.work_status, ai_body)

    return {
        "ok": True,
        "host": host,
        "summary": summary,
        "evidence": evidence,
        "providers": [r.tool for r in results],
        "entry": entry,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Incident endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/incidents")
async def list_incidents(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str = "open",
    limit: int = 20,
):
    """List open/investigating incidents."""
    from sqlalchemy import select, desc
    from app.models.workflow import Incident, IncidentMember
    from sqlalchemy import func

    stmt = (
        select(Incident)
        .where(Incident.status == status)
        .order_by(desc(Incident.updated_at))
        .limit(limit)
    )
    rows = await db.execute(stmt)
    incidents = rows.scalars().all()

    result = []
    for inc in incidents:
        # Count members
        cnt = await db.execute(
            select(func.count()).where(IncidentMember.incident_id == inc.id)
        )
        member_count = cnt.scalar() or 0
        result.append({
            "id": str(inc.id),
            "title": inc.title,
            "primary_host": inc.primary_host,
            "severity": inc.severity,
            "status": inc.status,
            "member_count": member_count,
            "created_at": inc.created_at.isoformat(),
            "updated_at": inc.updated_at.isoformat(),
        })
    return result


@router.get("/incidents/{incident_id}/timeline")
async def incident_timeline(
    incident_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Assemble the full timeline for an incident: alerts + comments + AI analyses."""
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.workflow import Incident, IncidentMember, AlertComment

    try:
        inc_id = _uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Ungültige Incident-ID")

    inc = await db.get(Incident, inc_id)
    if not inc:
        raise HTTPException(404, "Incident nicht gefunden")

    # ── 1. Get all member external_ids ────────────────────────────────────
    members_r = await db.execute(
        select(IncidentMember).where(IncidentMember.incident_id == inc_id)
        .order_by(IncidentMember.added_at)
    )
    members = members_r.scalars().all()
    ext_ids = [m.external_id for m in members]

    timeline: list[dict] = []

    # ── 2. Feed items from OpenSearch ─────────────────────────────────────
    if ext_ids:
        try:
            from app.services.feed_index import search_by_query
            q = " OR ".join(f'external_id:"{eid}"' for eid in ext_ids[:20])
            items = await search_by_query("cs-feed-*", q, size=50)
            for item in items:
                timeline.append({
                    "at": item.get("created_at", ""),
                    "kind": "alert",
                    "source": item.get("source", ""),
                    "severity": item.get("severity", ""),
                    "text": item.get("title", "")[:200],
                    "external_id": item.get("external_id"),
                })
        except Exception as e:
            log.debug("incident_timeline: feed items failed: %s", e)

    # ── 3. AlertComments (claim/status/AI/comments) ───────────────────────
    if ext_ids:
        comments_r = await db.execute(
            select(AlertComment)
            .where(AlertComment.external_id.in_(ext_ids))
            .order_by(AlertComment.created_at)
        )
        for c in comments_r.scalars().all():
            timeline.append({
                "at": c.created_at.isoformat(),
                "kind": c.kind,
                "source": "collaboration",
                "severity": "",
                "text": c.body[:300],
                "user": c.user_name,
            })

    # ── 4. AI analyses in the incident time window ────────────────────────
    try:
        from app.models.ai import AiAnalysis
        from sqlalchemy import and_
        from datetime import timedelta
        window_start = inc.created_at - timedelta(minutes=5)
        window_end = (inc.resolved_at or inc.updated_at) + timedelta(minutes=10)
        ai_r = await db.execute(
            select(AiAnalysis)
            .where(and_(
                AiAnalysis.run_at >= window_start,
                AiAnalysis.run_at <= window_end,
            ))
            .order_by(AiAnalysis.run_at)
            .limit(5)
        )
        for a in ai_r.scalars().all():
            findings = (a.findings or [])[:2]
            summary = "; ".join(
                f.get("title", "") for f in findings if f.get("title")
            ) or "KI-Analyse"
            timeline.append({
                "at": a.run_at.isoformat(),
                "kind": "ai_analysis",
                "source": "ki_agent",
                "severity": a.severity_summary or "",
                "text": summary[:300],
            })
    except Exception as e:
        log.debug("incident_timeline: ai_analyses failed: %s", e)

    # Sort by timestamp
    def _sort_key(e: dict) -> str:
        return e.get("at") or ""

    timeline.sort(key=_sort_key)

    return {
        "incident": {
            "id": str(inc.id),
            "title": inc.title,
            "primary_host": inc.primary_host,
            "severity": inc.severity,
            "status": inc.status,
            "created_at": inc.created_at.isoformat(),
            "member_count": len(ext_ids),
        },
        "timeline": timeline,
    }


@router.get("/incidents/{incident_id}/claude-prompt")
async def incident_claude_prompt(
    incident_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Build a ready-to-paste Claude CLI prompt from an incident.

    The handoff point: CentralStation prepares the evidence-rich briefing,
    the engineer pastes it into Claude CLI (with real shell access + human
    oversight) to actually investigate and fix. CentralStation does NOT
    execute — it briefs.
    """
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.workflow import Incident, IncidentMember, AlertComment

    try:
        inc_id = _uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Ungültige Incident-ID")

    inc = await db.get(Incident, inc_id)
    if not inc:
        raise HTTPException(404, "Incident nicht gefunden")

    host = inc.primary_host
    lines: list[str] = []
    lines.append(f"# Incident-Untersuchung: {host}")
    lines.append("")
    lines.append(f"Auf dem Host **{host}** ist ein Incident aufgetreten "
                 f"(Severity: {inc.severity}, seit {inc.created_at.strftime('%Y-%m-%d %H:%M')} UTC).")
    lines.append("Bitte untersuche die Ursache und schlage einen konkreten Fix vor. "
                 "Du hast Shell-Zugriff auf den Host. Arbeite read-only bis der Fix klar ist.")
    lines.append("")

    # ── Member alerts (from OpenSearch) ────────────────────────────────────
    mem_r = await db.execute(
        select(IncidentMember).where(IncidentMember.incident_id == inc_id)
        .order_by(IncidentMember.added_at)
    )
    ext_ids = [m.external_id for m in mem_r.scalars().all()]
    if ext_ids:
        lines.append("## Zugehörige Alerts")
        try:
            from app.services.feed_index import search_by_query
            q = " OR ".join(f'external_id:"{e}"' for e in ext_ids[:20])
            items = await search_by_query("cs-feed-*", q, size=30)
            for item in sorted(items, key=lambda i: i.get("created_at", "")):
                ts = (item.get("created_at") or "")[:19].replace("T", " ")
                sev = (item.get("severity") or "?").upper()
                src = item.get("source", "")
                title = (item.get("title") or "")[:160]
                lines.append(f"- `{ts}` [{sev}/{src}] {title}")
        except Exception as e:
            log.debug("claude-prompt: alerts failed: %s", e)
        lines.append("")

    # ── AI diagnosis evidence (from comment thread) ────────────────────────
    if ext_ids:
        com_r = await db.execute(
            select(AlertComment)
            .where(AlertComment.external_id.in_(ext_ids), AlertComment.kind == "ai")
            .order_by(AlertComment.created_at)
        )
        ai_comments = com_r.scalars().all()
        if ai_comments:
            lines.append("## Bisherige KI-Diagnose (read-only, mit Belegen)")
            for c in ai_comments[:3]:
                lines.append(c.body)
                lines.append("")

    # ── Past incidents for this host ───────────────────────────────────────
    try:
        from app.services.ai_agent.past_incidents import (
            find_similar_incidents, format_past_incidents_for_llm,
        )
        past = await find_similar_incidents(host, db, limit=3)
        if past:
            lines.append("## Frühere ähnliche Vorfälle")
            lines.append(format_past_incidents_for_llm(past))
            lines.append("")

    except Exception as e:
        log.debug("claude-prompt: past_incidents failed: %s", e)

    lines.append("## Auftrag")
    lines.append(f"1. Verifiziere die Belege oben auf {host} (Logs, Service-Status, Metriken).")
    lines.append("2. Nenne die wahrscheinlichste Ursache MIT Beleg — keine Spekulation ohne Daten.")
    lines.append("3. Schlage einen konkreten, reversiblen Fix vor und warte auf Freigabe, bevor du ihn ausführst.")

    prompt = "\n".join(lines)
    return {"incident_id": str(inc.id), "host": host, "prompt": prompt}


# ─────────────────────────────────────────────────────────────────────────────
# AI-assisted Jira ticket creation from a feed item
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_jira_connector(db: AsyncSession, user_id):
    """Return the user's Jira connector (personal preferred, else global)."""
    from app.models.connector import ConnectorConfig
    r = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.type == "jira",
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
        .order_by(ConnectorConfig.owner_user_id.is_(None), ConnectorConfig.updated_at.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def _feed_item_by_external_id(external_id: str) -> dict | None:
    from app.services.feed_index import search_by_query
    try:
        items = await search_by_query(
            index_pattern="cs-feed-*",
            query_string=f'external_id:"{external_id}"',
            size=1,
        )
        return items[0] if items else None
    except Exception:
        return None


@router.post("/{external_id}/ticket-draft")
async def ticket_draft(
    external_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """AI pre-fills a Jira ticket draft for a feed item (the slow part → spinner).

    Returns {summary, description, priority, project} — NOT yet created in Jira.
    """
    item = await _feed_item_by_external_id(external_id)
    if not item:
        raise HTTPException(404, "Feed-Eintrag nicht gefunden")

    meta = item.get("metadata") or {}
    host = meta.get("host") or meta.get("agent") or meta.get("container_name") or ""
    application = meta.get("application") or ""
    severity = item.get("severity", "info")
    title = item.get("title", "")
    body = (item.get("body") or "")[:600]
    source = item.get("source", "")
    ai_insight = item.get("ai_insight") or ""

    # Default project from user preferences
    prefs = await _get_prefs(user.id, db)
    default_project = (getattr(prefs, "jira_project", None) or "").strip()

    # Severity → Jira priority
    prio_map = {"critical": "Kritisch", "high": "Hoch", "medium": "Normal", "low": "Niedrig", "info": "Niedrig"}
    priority = prio_map.get(severity, "Normal")

    summary = ""
    description = ""
    try:
        from app.services.settings import get_active_llm_config
        from app.services.ai_language import with_language, get_response_language_for_user
        from app.services.llm_client import generate_text
        from app.services.dashboard.generative_designer import _strip_thinking
        import json as _json

        llm_cfg = await get_active_llm_config(db)
        if llm_cfg.is_configured:
            lang = await get_response_language_for_user(db, user.id)
            system_prompt = with_language(
                "You are an IT operations engineer creating a Jira ticket from a monitoring alert. "
                "Produce a concise, professional ticket. Return STRICT JSON only: "
                '{"summary": "<one line, max 120 chars>", "description": "<structured: Problem, '
                'Affected system, Likely cause if derivable, First steps. Use Jira wiki markup '
                '(*bold*, ---- separators). No invented facts.>"}. '
                "The 'log source' field names the collector (Graylog/CheckMK), NOT the failing system.",
                lang,
            )
            ctx = f"Source: {source}\nSeverity: {severity}\nHost: {host}\n"
            if application:
                ctx += f"Application: {application}\n"
            ctx += f"Alert: {title}\n"
            if body:
                ctx += f"Details: {body}\n"
            if ai_insight:
                ctx += f"Prior AI insight: {ai_insight}\n"
            raw = await generate_text(
                llm_cfg,
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": ctx}],
            )
            clean = _strip_thinking(raw)
            # Robustly extract the JSON object (ignores markdown fences / prose)
            lo, hi = clean.find("{"), clean.rfind("}")
            if lo >= 0 and hi > lo:
                try:
                    data = _json.loads(clean[lo:hi + 1])
                    summary = (data.get("summary") or "").strip()
                    description = (data.get("description") or "").strip()
                except Exception:
                    log.warning("ticket_draft: AI returned unparseable JSON, using template fallback")
    except Exception as e:
        log.warning("ticket_draft: AI prefill failed, using template fallback: %s", e)

    # Fallbacks if AI unavailable
    if not summary:
        host_part = f"{host} — " if host else ""
        summary = f"{severity.upper()}: {host_part}{title}"[:120]
    if not description:
        description = f"*Source:* {source}\n*Host:* {host}\n*Alert:* {title}\n\n{body}"

    return {
        "summary": summary,
        "description": description,
        "priority": priority,
        "project": default_project,
        "host": host,
    }


@router.post("/{external_id}/create-ticket")
async def create_ticket(
    external_id: str,
    body: dict,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create the Jira ticket from the (possibly edited) draft."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    summary = (body.get("summary") or "").strip()
    description = (body.get("description") or "").strip()
    project = (body.get("project") or "").strip()
    priority = (body.get("priority") or "Normal").strip()
    issue_type = (body.get("issue_type") or "Serviceanfrage").strip()

    if not summary or not project:
        raise HTTPException(400, "Summary und Projekt sind erforderlich")

    conn = await _resolve_jira_connector(db, user.id)
    if not conn:
        raise HTTPException(400, "Kein Jira-Connector konfiguriert")

    creds = decrypt_credentials(conn.encrypted_credentials)
    jira = JiraConnector(base_url=conn.base_url, credentials=creds)
    try:
        result = await jira.create_issue(
            project=project,
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority,
            labels=["centralstation"],
        )
    except Exception as e:
        raise HTTPException(502, f"Jira-Ticket konnte nicht erstellt werden: {str(e)[:200]}")

    jira_key = result.get("key", "")
    base = (conn.base_url or "").rstrip("/")
    url = f"{base}/browse/{jira_key}" if jira_key and base else None

    # Record in the collaboration timeline
    try:
        collab = await _get_or_create_collab(external_id, db)
        await _add_timeline(
            external_id, user.id, user.full_name or user.email, "comment",
            f"🎫 Jira-Ticket erstellt: {jira_key}", db,
        )
        await db.commit()
        await _broadcast_collab(external_id, "comment", user.full_name or user.email,
                                collab.work_status, f"Jira-Ticket erstellt: {jira_key}")
    except Exception as e:
        log.debug("create_ticket: timeline record failed: %s", e)

    return {"ok": True, "jira_key": jira_key, "url": url}
