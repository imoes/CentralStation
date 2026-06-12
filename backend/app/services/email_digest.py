"""Email digest — per-user daily/weekly KI-insight reports.

Logic:
- Scheduler calls run_digest_for_hour(db, now) every hour on the minute.
- For each active user with digest_daily / digest_weekly enabled in
  notification_settings, if now matches their configured send hour (and
  weekday for weekly), an HTML digest is built and sent.
- Digest content: all cs-feed-* documents that have an ai_insight, created
  within the look-back window, filtered to hosts from the user's CheckMK
  host scope, grouped by host → service.
- SMTP credentials come from the ConnectorConfig with type="smtp".
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEV_COLOR = {
    "critical": "#b71c1c",
    "high":     "#e65100",
    "medium":   "#f9a825",
    "low":      "#1565c0",
    "info":     "#546e7a",
}
_WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                  "Freitag", "Samstag", "Sonntag"]


async def _load_smtp(db: AsyncSession):
    """Load the first enabled SMTP connector, or None."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.smtp import SMTPConnector

    r = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "smtp",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    row = r.scalars().first()
    if not row:
        return None
    return SMTPConnector(
        base_url=row.base_url,
        credentials=decrypt_credentials(row.encrypted_credentials),
    )


async def _build_html(user: Any, hours: int, db: AsyncSession) -> str | None:
    """Query cs-feed-* for ai_insight items in the window, filtered by the
    user's CheckMK host scope.  Returns HTML string or None if nothing found."""
    from app.core.opensearch import get_opensearch
    from app.services.feed_index import get_user_checkmk_host_scope

    allowed_hosts = await get_user_checkmk_host_scope(db, str(user.id))
    # [] = no active filter → include all hosts
    # ["h1", …] = filter active → restrict to these hosts

    must: list[dict] = [
        {"exists": {"field": "ai_insight"}},
        {"range":  {"created_at": {"gte": f"now-{hours}h"}}},
    ]
    if allowed_hosts:
        must.append({"terms": {"metadata.host.keyword": allowed_hosts}})

    os_client = get_opensearch()
    try:
        resp = await os_client.search(
            index="cs-feed-*",
            body={
                "size": 500,
                "query": {"bool": {
                    "must": must,
                    "must_not": [{"term": {"status": "resolved"}}],
                }},
                "sort": [{"created_at": {"order": "desc"}}],
            },
            ignore_unavailable=True,
        )
    except Exception as exc:
        log.warning("digest: OpenSearch query failed: %s", exc)
        return None

    hits = (resp.get("hits") or {}).get("hits", [])
    if not hits:
        return None

    # Sort by severity (critical first), then group host → service
    hits_sorted = sorted(
        hits,
        key=lambda x: _SEV_ORDER.get(x["_source"].get("severity", "info"), 5),
    )
    grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for h in hits_sorted:
        src  = h["_source"]
        meta = src.get("metadata") or {}
        host = meta.get("host") or "Unknown"
        svc  = meta.get("service") or src.get("source") or "—"
        grouped[host][svc].append(src)

    return _render_html(grouped, hours, user)


def _render_html(
    grouped: dict[str, dict[str, list]],
    hours: int,
    user: Any,
) -> str:
    period = "letzten 24 Stunden" if hours <= 24 else "letzten 7 Tagen"
    name   = getattr(user, "full_name", None) or user.email

    rows_html = ""
    for host, services in sorted(grouped.items()):
        rows_html += f"""
        <tr>
          <td colspan="4" style="
            background:#FF9933;color:#000;font-weight:700;
            font-size:13px;letter-spacing:.08em;text-transform:uppercase;
            padding:6px 12px;border-radius:4px 4px 0 0;">
            {_esc(host)}
          </td>
        </tr>"""
        for svc, items in sorted(services.items()):
            rows_html += f"""
        <tr>
          <td colspan="4" style="
            background:#1a1200;color:#FFCC99;font-size:11px;
            padding:3px 12px 3px 20px;letter-spacing:.06em;text-transform:uppercase;">
            {_esc(svc)}
          </td>
        </tr>"""
            for item in items:
                sev   = item.get("severity", "info")
                color = _SEV_COLOR.get(sev, "#546e7a")
                title = item.get("title", "—")
                insight = item.get("ai_insight", "")
                url   = item.get("external_url", "")
                link  = (f'<a href="{_esc(url)}" style="color:#FFCC66;">'
                         f'→ Details</a>') if url else ""
                rows_html += f"""
        <tr style="border-bottom:1px solid #2a1d0a;">
          <td style="padding:4px 12px 4px 24px;width:70px;white-space:nowrap;">
            <span style="display:inline-block;padding:1px 8px;border-radius:3px;
              font-size:10px;font-weight:700;letter-spacing:.06em;
              color:{color};border:1px solid {color};">
              {sev.upper()}
            </span>
          </td>
          <td style="padding:4px 8px;color:#ffe8a0;font-size:12px;font-weight:600;">
            {_esc(title)}
          </td>
          <td style="padding:4px 8px;color:#FFCC99;font-size:11px;max-width:420px;">
            {_esc(insight)}
          </td>
          <td style="padding:4px 12px;font-size:11px;white-space:nowrap;">
            {link}
          </td>
        </tr>"""

    total = sum(len(i) for s in grouped.values() for i in s.values())

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>CentralStation Bericht</title></head>
<body style="margin:0;padding:0;background:#1a1200;font-family:Roboto,'Helvetica Neue',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
  style="max-width:780px;margin:24px auto;border:1px solid #3a2810;border-radius:8px;overflow:hidden;">

  <!-- Header -->
  <tr>
    <td style="background:#FF9933;padding:0;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:12px 20px;">
            <span style="font-size:18px;font-weight:700;color:#000;
              letter-spacing:.1em;text-transform:uppercase;">
              CENTRALSTATION
            </span>
            <span style="font-size:12px;color:#000;margin-left:12px;opacity:.7;">
              KI-Insight Bericht
            </span>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Sub-header -->
  <tr>
    <td style="background:#0a0804;padding:10px 20px;
      border-bottom:1px solid #3a2810;color:#FFCC99;font-size:12px;">
      Hallo {_esc(name)} — {total} KI-Insights aus den {period}
    </td>
  </tr>

  <!-- Table -->
  <tr><td style="padding:0;">
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#0a0804;border-collapse:collapse;">
      {rows_html}
    </table>
  </td></tr>

  <!-- Footer -->
  <tr>
    <td style="background:#000;padding:10px 20px;border-top:1px solid #3a2810;
      color:#666;font-size:10px;">
      Abonnement ändern: <b>CentralStation → Einstellungen → Mein Profil → E-Mail-Berichte</b>
    </td>
  </tr>
</table>
</body></html>"""


def _esc(s: Any) -> str:
    """Minimal HTML-escape."""
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


async def run_digest_for_hour(db: AsyncSession, now: datetime) -> None:
    """Called every full hour — sends digests to users whose configured time matches."""
    from sqlalchemy import select
    from app.models.user import User
    from app.models.workflow import UserPreference

    current_hour    = now.hour
    current_weekday = now.weekday()   # 0 = Monday, 6 = Sunday

    smtp = await _load_smtp(db)
    if not smtp:
        log.debug("digest: no SMTP connector configured, skipping")
        return

    result = await db.execute(
        select(User, UserPreference)
        .join(UserPreference, UserPreference.user_id == User.id, isouter=True)
        .where(User.is_active.is_(True))
    )

    for user, prefs in result.all():
        if prefs is None:
            continue
        ns = prefs.notification_settings or {}

        try:
            # Daily digest
            if ns.get("digest_daily") and int(ns.get("digest_daily_hour", 7)) == current_hour:
                html = await _build_html(user, 24, db)
                if html:
                    await smtp.send(
                        user.email,
                        "CentralStation — Täglicher KI-Insight Bericht",
                        html,
                    )
                    log.info("digest: daily sent to %s", user.email)

            # Weekly digest
            if (ns.get("digest_weekly")
                    and int(ns.get("digest_weekly_day", 0)) == current_weekday
                    and int(ns.get("digest_weekly_hour", 7)) == current_hour):
                html = await _build_html(user, 168, db)
                if html:
                    await smtp.send(
                        user.email,
                        "CentralStation — Wöchentlicher KI-Insight Bericht",
                        html,
                    )
                    log.info("digest: weekly sent to %s", user.email)

        except Exception as exc:
            log.warning("digest: send failed for %s: %s", user.email, exc)
