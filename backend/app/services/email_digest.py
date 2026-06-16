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
    """Build digest HTML from ai_analyses records in the DB.

    The AI agent writes structured findings + recommendations to ai_analyses
    every interval (default 60 min). This is the canonical source for the
    digest — no per-alert ai_insight enrichment required.
    """
    from sqlalchemy import select
    from datetime import datetime, timezone, timedelta
    from app.models.ai import AiAnalysis
    from app.services.feed_index import get_user_checkmk_host_scope

    allowed_hosts = set(await get_user_checkmk_host_scope(db, str(user.id)))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await db.execute(
        select(AiAnalysis)
        .where(AiAnalysis.run_at >= cutoff)
        .order_by(AiAnalysis.run_at.desc())
    )
    analyses = result.scalars().all()
    if not analyses:
        return None

    # Collect all findings, optionally filtered by host scope
    all_findings: list[dict] = []
    for a in analyses:
        for f in (a.findings or []):
            if allowed_hosts:
                text_to_scan = f"{f.get('title', '')} {f.get('description', '')}".lower()
                if not any(h.lower() in text_to_scan for h in allowed_hosts):
                    continue
            all_findings.append({**f, "_run_at": a.run_at})

    # Collect recommendations from the most recent analysis only
    latest = analyses[0]
    recommendations = latest.recommendations or []

    if not all_findings and not recommendations:
        return None

    return _render_analysis_html(all_findings, recommendations, hours, user)


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


def _render_analysis_html(
    findings: list[dict],
    recommendations: list[dict],
    hours: int,
    user: Any,
) -> str:
    period = "letzten 24 Stunden" if hours <= 24 else "letzten 7 Tagen"
    name   = getattr(user, "full_name", None) or user.email

    # Sort findings by severity
    findings_sorted = sorted(
        findings,
        key=lambda f: _SEV_ORDER.get(f.get("severity", "info"), 5),
    )

    findings_html = ""
    for f in findings_sorted:
        sev   = f.get("severity", "info")
        color = _SEV_COLOR.get(sev, "#546e7a")
        title = _esc(f.get("title", "—"))
        desc  = _esc(f.get("description", ""))
        findings_html += f"""
        <tr style="border-bottom:1px solid #2a1d0a;">
          <td style="padding:6px 12px;width:70px;vertical-align:top;white-space:nowrap;">
            <span style="display:inline-block;padding:1px 8px;border-radius:3px;
              font-size:10px;font-weight:700;letter-spacing:.06em;
              color:{color};border:1px solid {color};">
              {sev.upper()}
            </span>
          </td>
          <td style="padding:6px 8px;vertical-align:top;">
            <div style="color:#ffe8a0;font-size:12px;font-weight:600;margin-bottom:4px;">{title}</div>
            <div style="color:#FFCC99;font-size:11px;line-height:1.5;">{desc}</div>
          </td>
        </tr>"""

    recs_html = ""
    for r in recommendations:
        pri   = r.get("priority", "medium")
        color = _SEV_COLOR.get(pri, "#546e7a")
        action = _esc(r.get("action", ""))
        recs_html += f"""
        <tr style="border-bottom:1px solid #1a1a00;">
          <td style="padding:5px 12px;width:70px;vertical-align:top;white-space:nowrap;">
            <span style="display:inline-block;padding:1px 8px;border-radius:3px;
              font-size:10px;font-weight:700;color:{color};border:1px solid {color};">
              {pri.upper()}
            </span>
          </td>
          <td style="padding:5px 8px;color:#FFCC99;font-size:11px;line-height:1.5;">
            {action}
          </td>
        </tr>"""

    recs_section = f"""
  <tr>
    <td style="background:#0a0804;padding:8px 20px;border-top:1px solid #3a2810;">
      <div style="color:#FF9933;font-size:11px;font-weight:700;letter-spacing:.08em;
        text-transform:uppercase;margin-bottom:4px;">Empfehlungen</div>
    </td>
  </tr>
  <tr><td style="padding:0;">
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#0d0d00;border-collapse:collapse;">
      {recs_html}
    </table>
  </td></tr>""" if recs_html else ""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>CentralStation Bericht</title></head>
<body style="margin:0;padding:0;background:#1a1200;font-family:Roboto,'Helvetica Neue',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
  style="max-width:780px;margin:24px auto;border:1px solid #3a2810;border-radius:8px;overflow:hidden;">

  <tr>
    <td style="background:#FF9933;padding:0;">
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="padding:12px 20px;">
          <span style="font-size:18px;font-weight:700;color:#000;
            letter-spacing:.1em;text-transform:uppercase;">CENTRALSTATION</span>
          <span style="font-size:12px;color:#000;margin-left:12px;opacity:.7;">KI-Analyse Bericht</span>
        </td>
      </tr></table>
    </td>
  </tr>

  <tr>
    <td style="background:#0a0804;padding:10px 20px;
      border-bottom:1px solid #3a2810;color:#FFCC99;font-size:12px;">
      Hallo {_esc(name)} — {len(findings_sorted)} Befunde aus den {period}
    </td>
  </tr>

  <tr>
    <td style="background:#0a0804;padding:8px 20px;">
      <div style="color:#FF9933;font-size:11px;font-weight:700;letter-spacing:.08em;
        text-transform:uppercase;margin-bottom:4px;">Befunde</div>
    </td>
  </tr>
  <tr><td style="padding:0;">
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#0a0804;border-collapse:collapse;">
      {findings_html}
    </table>
  </td></tr>

  {recs_section}

  <tr>
    <td style="background:#000;padding:10px 20px;border-top:1px solid #3a2810;
      color:#666;font-size:10px;">
      Abonnement ändern: <b>CentralStation → Einstellungen → Mein Profil → E-Mail-Berichte</b>
    </td>
  </tr>
</table>
</body></html>"""


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
