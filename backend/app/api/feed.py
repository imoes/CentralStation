"""News Feed — aggregates monitoring alerts, emails, and Teams messages."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.workflow import UserPreference

router = APIRouter(prefix="/feed", tags=["feed"])

_ALL_SOURCES = ["checkmk", "graylog", "wazuh", "o365", "teams"]


async def _get_prefs(user_id, db: AsyncSession) -> UserPreference | None:
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    return result.scalar_one_or_none()


@router.get("/")
async def get_feed(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    sources: str | None = Query(None, description="Comma-separated source filter"),
    severity: str | None = Query(None),
    host: str | None = Query(None, description="Filter by hostname/title substring (CheckMK)"),
    os: str | None = Query(None, description="Filter by OS tag (CheckMK metadata)"),
    location: str | None = Query(None, description="Filter by location tag"),
    criticality: str | None = Query(None, description="Filter by criticality tag"),
    ve: str | None = Query(None, description="Filter by VE/environment tag"),
):
    """Return unified news feed sorted by created_at descending."""
    prefs = await _get_prefs(user.id, db)

    min_age = (prefs.feed_checkmk_min_age_minutes if prefs else None) or 5
    enabled = (prefs.feed_sources_enabled if prefs else None) or ["checkmk", "graylog", "wazuh"]

    requested = sources.split(",") if sources else enabled
    active = [s.strip() for s in requested if s.strip() in _ALL_SOURCES]

    items: list[dict] = []

    # ── Monitoring alerts from DB ────────────────────────────────────────────
    db_sources = [s for s in active if s in ("checkmk", "graylog", "wazuh")]
    if db_sources:
        q = select(Alert).where(Alert.source.in_(db_sources))

        if "checkmk" in db_sources:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age)
            q = q.where(
                (Alert.source != "checkmk") | (Alert.created_at <= cutoff)
            )

        if severity:
            q = q.where(Alert.severity == severity)

        if host:
            q = q.where(Alert.title.ilike(f"%{host}%"))

        # JSON metadata filters (CheckMK tag groups)
        if os:
            q = q.where(Alert.metadata_["os"].astext.ilike(f"%{os}%"))
        if location:
            q = q.where(Alert.metadata_["location"].astext.ilike(f"%{location}%"))
        if criticality:
            q = q.where(Alert.metadata_["criticality"].astext == criticality)
        if ve:
            q = q.where(Alert.metadata_["ve"].astext == ve)

        q = q.order_by(Alert.created_at.desc()).limit(limit + offset)
        rows = (await db.execute(q)).scalars().all()

        for a in rows:
            items.append(
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
                    "external_url": None,
                }
            )

    # ── O365 Mail ────────────────────────────────────────────────────────────
    if "o365" in active and prefs and prefs.o365_mailbox:
        try:
            from app.core.security import decrypt_credentials
            from app.models.connector import ConnectorConfig
            from app.services.connectors.o365 import O365Connector

            r = await db.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.type == "o365",
                    ConnectorConfig.enabled.is_(True),
                ).limit(1)
            )
            connector = r.scalar_one_or_none()
            if connector:
                creds = decrypt_credentials(connector.encrypted_credentials)
                o365 = O365Connector(connector.base_url, creds)
                mails = await o365.get_unread_mails(
                    prefs.o365_mailbox,
                    prefs.o365_folder or "Inbox",
                    top=10,
                )
                for mail in mails:
                    sender = (
                        mail.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "")
                    )
                    items.append(
                        {
                            "id": f"mail_{mail.get('id', '')}",
                            "type": "email",
                            "source": "o365",
                            "severity": "info",
                            "title": mail.get("subject") or "(kein Betreff)",
                            "body": mail.get("bodyPreview", ""),
                            "metadata": {
                                "from": sender,
                                "received_at": mail.get("receivedDateTime", ""),
                            },
                            "created_at": mail.get("receivedDateTime", ""),
                            "status": "new",
                            "location_name": None,
                            "location_city": None,
                            "external_url": mail.get("webLink"),
                        }
                    )
        except Exception:
            pass  # graceful degradation

    # ── Teams messages ───────────────────────────────────────────────────────
    channels = (prefs.feed_teams_channels if prefs else None) or []
    if "teams" in active and channels:
        try:
            from app.core.security import decrypt_credentials
            from app.models.connector import ConnectorConfig
            from app.services.connectors.teams import TeamsConnector

            r = await db.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.type == "teams",
                    ConnectorConfig.enabled.is_(True),
                ).limit(1)
            )
            connector = r.scalar_one_or_none()
            if connector:
                creds = decrypt_credentials(connector.encrypted_credentials)
                tc = TeamsConnector(connector.base_url, creds)
                for channel_id in channels[:5]:
                    msgs = await tc.get_channel_messages(channel_id, top=5)
                    for msg in msgs:
                        items.append(
                            {
                                "id": f"teams_{msg.get('id', '')}",
                                "type": "teams_message",
                                "source": "teams",
                                "severity": "info",
                                "title": msg.get("channelName", "Teams"),
                                "body": msg.get("body", {}).get("content", ""),
                                "metadata": {
                                    "from": msg.get("from", {})
                                    .get("user", {})
                                    .get("displayName", ""),
                                    "channel_id": channel_id,
                                },
                                "created_at": msg.get("createdDateTime", ""),
                                "status": "new",
                                "location_name": None,
                                "location_city": None,
                                "external_url": msg.get("webUrl"),
                            }
                        )
        except Exception:
            pass

    # Sort all by created_at desc
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return items[offset : offset + limit]


@router.get("/checkmk-filter-values")
async def get_checkmk_filter_values(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return distinct OS, location, criticality and VE values from CheckMK alerts in DB."""
    from sqlalchemy import func, cast, String
    from sqlalchemy.dialects.postgresql import JSONB

    q = select(Alert.metadata_).where(
        Alert.source == "checkmk",
        Alert.metadata_.isnot(None),
    )
    rows = (await db.execute(q)).scalars().all()

    os_vals: set[str] = set()
    loc_vals: set[str] = set()
    crit_vals: set[str] = set()
    ve_vals: set[str] = set()

    for m in rows:
        if not isinstance(m, dict):
            continue
        if v := m.get("os"):
            os_vals.add(v)
        if v := m.get("location"):
            loc_vals.add(v)
        if v := m.get("criticality"):
            crit_vals.add(v)
        if v := m.get("ve"):
            ve_vals.add(v)

    return {
        "os": sorted(os_vals - {""}),
        "location": sorted(loc_vals - {""}),
        "criticality": sorted(crit_vals - {""}),
        "ve": sorted(ve_vals - {""}),
    }


@router.post("/{alert_id}/acknowledge")
async def acknowledge_feed_item(
    alert_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Acknowledge a monitoring alert from the feed."""
    import uuid as _uuid

    try:
        uid = _uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(400, "Invalid alert ID")

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
    return {"ok": True}
