"""Meldungen der Fehler-Cluster-Erkennung: KI-Einschätzung und Sofort-Mail.

Reihenfolge je meldepflichtigem Ereignis (Regeln haben entschieden, nicht das LLM):
1. KI-Einschätzung anstoßen; höchstens TRIAGE_WAIT abwarten.
2. Mail SOFORT an alle angemeldeten Nutzer, deren Host-Scope den Cluster berührt —
   mit Einschätzung, oder mit "KI-Einschätzung folgt".
3. Kam die Einschätzung zu spät oder gar nicht: sobald sie da ist, eine zweite Mail.
Ein LLM-Ausfall verzögert die Meldung also nie. Die Einschätzung landet zusätzlich im
Cluster, im Incident und als AiAnalysis (Seite "KI-Analysen", Tagesübersicht).

Mails gehen nur bei error_clusters.mail_enabled raus (Not-Aus) und nur an Nutzer mit
notification_settings.error_cluster_immediate.
"""
from __future__ import annotations

import asyncio
import html
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

log = logging.getLogger(__name__)

TRIAGE_WAIT = 240.0         # so lange wartet die Mail höchstens auf die KI
#: Gemessen 01./02.10.: die Einschätzung brauchte 40–105 s. Bei 90 s kam sie viermal
#: knapp danach — und mit ihr eine zweite Mail, die denselben Vorfall nur anders
#: formulierte. 240 s deckt das ab; der Vorfall bleibt trotzdem binnen Minuten gemeldet.
APP_URL_SETTING = "app.public_url"


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def fmt_int(n: int | float | None) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def fmt_rate(count: float | None, seconds: float) -> str:
    """Häufigkeit als Rate: "16 msg/s", "4,2 msg/min", "3 msg/h".

    Dieselben Stufen wie im Frontend (shared/format-rate.ts) — eine Regel, überall gleich.
    """
    if not count or seconds <= 0:
        return "0 msg/s"
    per_sec = count / seconds

    def f(v: float) -> str:
        return (f"{v:.0f}" if v >= 10 else f"{v:.1f}".rstrip("0").rstrip(".")).replace(".", ",")

    if per_sec >= 1:
        return f"{f(per_sec)} msg/s"
    if per_sec * 60 >= 1:
        return f"{f(per_sec * 60)} msg/min"
    return f"{f(per_sec * 3600)} msg/h"


def subject(ctx: dict, title: str, followup: bool = False) -> str:
    n_cont = len(ctx.get("containers") or [])
    head = "KI-Einschätzung" if followup else "Fehlerausbruch"
    short = title.split(": ", 1)[-1].split(" · ")[0]
    return (f"[CentralStation] {head}: {short} · {n_cont} Container · "
            f"{fmt_rate(ctx.get('count15'), 900)}")


def render_html(ctx: dict, triage: dict | None, incident_url: str | None,
                pending: bool) -> str:
    """Deutsche Mail. Inhalt vor Chrom: KI-Einschätzung, Muster, Verlauf, Betroffene."""
    rows = []
    if triage:
        steps = "".join(f"<li>{_esc(s)}</li>" for s in triage.get("pruefschritte") or [])
        signals = "".join(f"<li>{_esc(s)}</li>" for s in triage.get("verwandte_signale") or [])
        rows.append(f"""
        <h3 style="margin:0 0 6px">{_esc(triage.get('titel'))}</h3>
        <p style="margin:0 0 8px">{_esc(triage.get('zusammenfassung'))}</p>
        <p style="margin:0 0 4px"><b>Vermutete Ursache:</b> {_esc(triage.get('vermutete_ursache'))}
           <span style="color:#777">(Konfidenz: {_esc(triage.get('konfidenz'))})</span></p>
        <p style="margin:0 0 4px"><b>Betroffene Komponente:</b> {_esc(triage.get('betroffene_komponente'))}</p>
        {f'<p style="margin:8px 0 2px"><b>Prüfschritte (nur lesend):</b></p><ol>{steps}</ol>' if steps else ''}
        {f'<p style="margin:8px 0 2px"><b>Verwandte Signale:</b></p><ul>{signals}</ul>' if signals else ''}""")
    elif pending:
        rows.append('<p style="color:#777"><i>KI-Einschätzung folgt in einer zweiten Mail.</i></p>')
    else:
        rows.append('<p style="color:#777"><i>KI-Einschätzung nicht verfügbar.</i></p>')

    rec = ctx.get("recipe")
    if rec:
        steps = "".join(f"<li>{_esc(st.get('template'))} {_esc(st.get('params') or '')} — "
                        f"{_esc(st.get('why') or '')}</li>" for st in rec.get("steps") or [])
        rows.append(f"""
        <div style="margin:10px 0;padding:8px;border:1px solid #9c6;border-radius:6px">
        <b>Bewährtes Rezept aus einem früheren Vorfall:</b> {_esc(rec.get('title'))}<br>
        <span style="color:#555">Lösung damals: {_esc(rec.get('solution'))}</span>
        {f'<ol>{steps}</ol>' if steps else ''}
        <span style="color:#777;font-size:12px">Ausführen (nur lesend): Incident öffnen → „Prüfschritte ausführen".</span>
        </div>""")

    containers = ctx.get("containers") or []
    cont_html = "".join(f"<tr><td>{_esc(c['name'])}</td><td style='text-align:right'>"
                        f"{fmt_int(c['count'])}</td></tr>" for c in containers[:15])
    more = f"<p style='color:#777'>… und {len(containers) - 15} weitere</p>" if len(containers) > 15 else ""
    examples = "".join(f"<pre style='white-space:pre-wrap;background:#f4f4f4;padding:6px;"
                       f"margin:4px 0;font-size:12px'>{_esc(e)}</pre>"
                       for e in (ctx.get("examples") or [])[:3])
    reasons = "".join(f"<li>{_esc(r)}</li>" for r in ctx.get("reasons") or [])
    links = []
    if incident_url:
        links.append(f'<a href="{_esc(incident_url)}">Incident öffnen (Timeline, An Konsole übergeben)</a>')
    if ctx.get("graylog_url"):
        links.append(f'<a href="{_esc(ctx["graylog_url"])}">In Graylog ansehen</a>')
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;max-width:760px">
    {''.join(rows)}
    <h4 style="margin:16px 0 4px">Warum gemeldet</h4><ul>{reasons}</ul>
    <h4 style="margin:12px 0 4px">Muster</h4>
    <pre style="white-space:pre-wrap;background:#f4f4f4;padding:6px;font-size:12px">{_esc(ctx.get('template'))}</pre>
    <p style="margin:4px 0">Seit {_esc(_hm(ctx.get('started_at')))} UTC · {fmt_int(ctx.get('total'))} Zeilen gesamt ·
       zuletzt <b>{fmt_rate(ctx.get('count15'), 900)}</b> ({fmt_int(ctx.get('count15'))} in 15 min,
       erwartet {fmt_rate(ctx.get('expected15') or 0, 900)})</p>
    <h4 style="margin:12px 0 4px">Betroffen ({len(containers)} Container)</h4>
    <table style="border-collapse:collapse;font-size:13px">{cont_html}</table>{more}
    <h4 style="margin:12px 0 4px">Beispiele</h4>{examples}
    <p style="margin:14px 0">{' · '.join(links)}</p>
    <p style="color:#888;font-size:12px;margin-top:18px">Automatisch erkannt – keine Aktion ausgeführt.
       Abbestellen: Einstellungen → E-Mail-Berichte.</p></div>"""


def _hm(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        return str(iso or "")


async def recipients(db, hosts: list[str]) -> list[str]:
    """Angemeldete Nutzer, deren Host-Scope den Cluster berührt (leerer Scope = alles)."""
    from app.models.user import User
    from app.models.workflow import UserPreference
    from app.services.error_clusters.incident import host_matches
    from app.services.feed_index import get_user_checkmk_host_scope

    rows = await db.execute(select(User, UserPreference)
                            .join(UserPreference, UserPreference.user_id == User.id))
    out = []
    for user, prefs in rows.all():
        ns = prefs.notification_settings or {}
        if not ns.get("error_cluster_immediate") or not getattr(user, "is_active", True):
            continue
        if not user.email:
            continue
        scope = await get_user_checkmk_host_scope(db, str(user.id))
        if scope and not any(host_matches(s, hosts) for s in scope):
            continue
        out.append(user.email)
    return sorted(set(out))


async def _correlated(rt, ev, limit: int = 10) -> list[dict]:
    """Andere Container, die im selben 15-min-Fenster ≥ 3× über ihrem 48-h-Median liegen."""
    from app.services.error_clusters.engine import BUCKET
    from app.services.error_clusters.rules import median_with_zeros
    b = ev.bucket_start
    window = [b - BUCKET * 2, b - BUCKET, b]
    own = set(ev.episode.entities)
    out = []
    for ent, series in rt.store.entity_buckets.items():
        if ent in own:
            continue
        now15 = sum(series.get(x, 0) for x in window)
        if now15 < 30:
            continue
        hist = [v for k, v in series.items() if k < window[0]]
        n = max(1, int((window[0] - min(series)) / BUCKET)) if series else 1
        lam = max(median_with_zeros(hist, n), 0.2)
        factor = now15 / (3 * lam)
        if factor >= 3:
            out.append({"container": ent, "zeilen_15min": now15, "faktor": round(factor, 1)})
    out.sort(key=lambda x: -x["faktor"])
    return out[:limit]


async def _checkmk(db, hosts: list[str], since: datetime) -> list[dict]:
    from app.models.alert import Alert
    from app.services.error_clusters.incident import host_matches
    rows = await db.execute(select(Alert).where(Alert.source == "checkmk",
                                                Alert.created_at >= since))
    out = []
    for a in rows.scalars():
        h = (a.metadata_ or {}).get("host", "")
        if h and host_matches(h, hosts):
            out.append({"host": h, "titel": a.title, "schwere": a.severity,
                        "zeit": a.created_at.isoformat()})
    return out[:15]


async def _store_triage(db, cluster_id: str, incident_id: str | None, result: dict, ctx: dict):
    from app.models.ai import AiAnalysis
    from app.models.error_cluster import ErrorCluster
    from app.services.error_clusters.incident import cluster_context, set_cluster_context
    from app.models.workflow import Incident
    now = datetime.now(timezone.utc)
    ec = (await db.execute(select(ErrorCluster).where(ErrorCluster.id == uuid.UUID(cluster_id)))
          ).scalar_one_or_none()
    if ec:
        ec.triage, ec.triage_at = result, now
    if incident_id:
        inc = (await db.execute(select(Incident).where(Incident.id == uuid.UUID(incident_id)))
               ).scalar_one_or_none()
        if inc:
            cur = cluster_context(inc) or {}
            set_cluster_context(inc, {**{k: v for k, v in cur.items() if k != "type"},
                                      "triage": result})
    sev = {"kritisch": "critical", "hoch": "high", "mittel": "medium"}.get(
        str(result.get("schweregrad")), "high")
    db.add(AiAnalysis(
        agent_type="error_cluster",
        sources_checked={"cluster_id": cluster_id, "incident_id": incident_id,
                         "containers": len(ctx.get("containers") or []),
                         "total": ctx.get("total")},
        findings=[{"title": result.get("titel"), "description": result.get("zusammenfassung"),
                   "severity": sev, "cause": result.get("vermutete_ursache"),
                   "component": result.get("betroffene_komponente"),
                   "hosts": ctx.get("hosts")}],
        recommendations=[{"title": s} for s in result.get("pruefschritte") or []],
        clusters=[{"template": ctx.get("template"), "rules": ctx.get("rules")}],
        severity_summary=sev,
    ))


async def _public_url(db) -> str:
    from app.services.settings import get_setting
    return (await get_setting(db, APP_URL_SETTING) or "https://hal.example.com").rstrip("/")


async def _send(db, to: list[str], subj: str, body: str) -> int:
    from app.services.email_digest import _load_smtp
    smtp = await _load_smtp(db)
    if smtp is None or not to:
        return 0
    sent = 0
    for addr in to:
        try:
            await smtp.send(addr, subj, body)
            sent += 1
        except Exception as exc:
            log.warning("error_clusters mail to %s failed: %s", addr, exc)
    return sent


async def _notify_one(ev, rt, cfg, base_url: str) -> None:
    """Log-Cluster: Kontext, gleichzeitig auffällige Container, CheckMK-Alerts → deliver."""
    from app.core.database import AsyncSessionLocal
    from app.services.error_clusters.runner import cluster_context, short_title

    ctx = cluster_context(ev, base_url)
    incident_id = rt.incident_ids.get(ev.episode.id)
    async with AsyncSessionLocal() as db:
        correlated = await _correlated(rt, ev)
        checkmk = await _checkmk(db, ctx["hosts"], ev.episode.started_at - timedelta(minutes=15))
        if incident_id and correlated:
            # Für Timeline und Konsolen-Übergabe: was gleichzeitig auffiel.
            from app.models.workflow import Incident
            from app.services.error_clusters.incident import cluster_context as _cc, set_cluster_context
            inc = (await db.execute(select(Incident).where(Incident.id == uuid.UUID(incident_id)))
                   ).scalar_one_or_none()
            if inc is not None:
                cur = _cc(inc) or {}
                set_cluster_context(inc, {**{k: v for k, v in cur.items() if k != "type"},
                                          "correlated": correlated})
                await db.commit()
    ctx["correlated"] = correlated
    await deliver(ctx, f"Fehlerausbruch: {short_title(ev.template.template)}", ev.episode.id,
                  incident_id, cfg, correlated, checkmk)


async def deliver(ctx: dict, title: str, cluster_id: str, incident_id: str | None, cfg,
                  correlated: list[dict] | None = None, checkmk: list[dict] | None = None) -> None:
    """EIN Zustellweg für alle Cluster-Arten: KI-Einschätzung (höchstens TRIAGE_WAIT
    abwarten), Sofort-Mail an den Host-Scope, Einschätzung bei Verspätung nachreichen."""
    from app.core.database import AsyncSessionLocal
    from app.services.error_clusters.triage import triage

    async with AsyncSessionLocal() as db:
        app_url = await _public_url(db)
        to = await recipients(db, ctx.get("hosts") or []) if cfg.mail_enabled else []
        try:
            from app.services.recipes import for_cluster, summary_of
            ctx["recipe"] = summary_of(await for_cluster(db, cluster_id))
        except Exception as exc:  # noqa: BLE001 — ohne Rezept geht die Mail trotzdem
            log.warning("error_clusters: recipe lookup failed: %s", exc)

    async def _run_triage():
        async with AsyncSessionLocal() as tdb:
            return await triage(tdb, ctx, correlated or [], checkmk or [])

    task = asyncio.create_task(_run_triage())
    result = None
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=TRIAGE_WAIT)
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        log.warning("error_clusters triage failed: %s", exc)
        task = None

    incident_url = f"{app_url}/feed?incident={incident_id}" if incident_id else None
    async with AsyncSessionLocal() as db:
        if result:
            await _store_triage(db, cluster_id, incident_id, result, ctx)
        sent = await _send(db, to, subject(ctx, title),
                           render_html(ctx, result, incident_url,
                                       pending=task is not None and result is None))
        await _mark_mailed(db, cluster_id)
        await db.commit()
    log.info("error_clusters: notify %s → %d mail(s), triage=%s", cluster_id[:8], sent,
             "ok" if result else ("pending" if task else "none"))

    if result is None and task is not None:
        try:
            late = await task
        except Exception as exc:
            log.warning("error_clusters late triage failed: %s", exc)
            return
        if not late:
            return
        async with AsyncSessionLocal() as db:
            await _store_triage(db, cluster_id, incident_id, late, ctx)
            await _send(db, to, subject(ctx, title, followup=True),
                        render_html(ctx, late, incident_url, pending=False))
            await db.commit()


async def on_events(events, rt, cfg, base_url: str) -> None:
    """Je meldepflichtigem Ereignis im Hintergrund melden — der Runner wartet nicht.

    Ein neuer Ausbruch stößt außerdem das generative Lagebild an, statt bis zu 15 min
    auf dessen Takt zu warten. Dessen eigene Grenzen (generative_enabled, nur kürzlich
    angesehene Lagebilder) gelten weiter.
    """
    from app.core.tasks import run_background
    mails = [ev for ev in events if ev.mail]
    for ev in mails:
        run_background(_notify_one(ev, rt, cfg, base_url), name=f"error_cluster_notify_{ev.episode.id[:8]}")
    if any(ev.action in ("open", "reopen") for ev in mails):
        from app.services.ai_agent.scheduler import run_generative_refresh
        run_background(run_generative_refresh(), name="error_cluster_generative_refresh")


async def build_digest_html(db, user_id: str, since: datetime) -> str | None:
    """Tagesübersicht: neue, ruhige Fehlermuster (keine Sofort-Mail) seit `since`.

    Gefiltert auf den Host-Scope des Nutzers wie die Sofort-Mails. None = nichts Neues.
    """
    from app.models.error_cluster import ErrorCluster, LogTemplate
    from app.services.error_clusters.incident import host_matches
    from app.services.error_clusters.runner import short_title
    from app.services.feed_index import get_user_checkmk_host_scope

    rows = (await db.execute(
        select(ErrorCluster, LogTemplate)
        .join(LogTemplate, LogTemplate.key == ErrorCluster.key)
        .where(ErrorCluster.notify_class == "digest", ErrorCluster.started_at >= since,
               LogTemplate.muted.is_(False))
        .order_by(ErrorCluster.total.desc()))).all()
    scope = await get_user_checkmk_host_scope(db, user_id)
    from app.services.error_clusters.runner import affected_hosts
    items = [(ec, t) for ec, t in rows
             if not scope or any(host_matches(s, affected_hosts(ec.sources, ec.entities))
                                 for s in scope)]
    if not items:
        return None
    trs = "".join(
        f"<tr><td style='padding:4px 8px'>{_esc(short_title(t.template))}</td>"
        f"<td style='padding:4px 8px;white-space:nowrap'>{_esc(ec.started_at.strftime('%d.%m. %H:%M'))}</td>"
        f"<td style='padding:4px 8px;text-align:right'>{fmt_int(ec.total)}</td>"
        f"<td style='padding:4px 8px'>{_esc(', '.join(list((ec.entities or {}).keys())[:3]))}</td>"
        f"<td style='padding:4px 8px'>{_esc(((ec.triage or {}).get('zusammenfassung') or '')[:160])}</td></tr>"
        for ec, t in items[:40])
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;max-width:900px">
    <h3>Neue Fehlermuster (letzte 24 Stunden)</h3>
    <p style="color:#555">Muster, die es 14 Tage nicht gab, aber ohne Ausbruch — zum Durchsehen,
       nicht zum sofortigen Handeln. {len(items)} Muster{'' if len(items) <= 40 else ', die 40 größten gezeigt'}.</p>
    <table style="border-collapse:collapse;font-size:13px">
      <tr style="background:#eee"><th style="padding:4px 8px;text-align:left">Muster</th>
      <th style="padding:4px 8px">Erstmals</th><th style="padding:4px 8px">Zeilen</th>
      <th style="padding:4px 8px;text-align:left">Container</th><th style="padding:4px 8px;text-align:left">KI-Kurzfazit</th></tr>
      {trs}</table>
    <p style="color:#888;font-size:12px;margin-top:18px">Automatisch erkannt – keine Aktion ausgeführt.
       Abbestellen: Einstellungen → E-Mail-Berichte.</p></div>"""


async def on_fleet(items: list[dict], cfg) -> None:
    """Flotten-Cluster (fleet.run) über denselben Zustellweg melden."""
    from app.core.tasks import run_background
    for it in items:
        run_background(deliver(it["ctx"], it["title"], it["cluster_id"], it["incident_id"], cfg,
                               [], [{"host": h} for h in it["ctx"].get("hosts", [])][:15]),
                       name=f"error_cluster_fleet_{it['cluster_id'][:8]}")
    if items:
        from app.services.ai_agent.scheduler import run_generative_refresh
        run_background(run_generative_refresh(), name="error_cluster_generative_refresh")


async def _mark_mailed(db, cluster_id: str) -> None:
    from app.models.error_cluster import ErrorCluster
    from sqlalchemy import update
    try:
        cid = uuid.UUID(cluster_id)
    except ValueError:
        return                     # Test-Zustellung ohne echte Episode
    await db.execute(update(ErrorCluster).where(ErrorCluster.id == cid)
                     .values(mailed_at=datetime.now(timezone.utc)))


def ctx_from_row(ec, t) -> dict:
    """Mail-Kontext aus einer gespeicherten Episode — für das Nachholen nach Neustart."""
    from app.services.error_clusters.runner import affected_hosts
    entities = ec.entities or {}
    return {
        "template": t.template if t else "",
        "examples": (t.examples or [])[:5] if t else [],
        "rules": ec.rules or [], "reasons": ec.reasons or [],
        "count15": ec.peak15, "expected15": None, "total": ec.total,
        "started_at": ec.started_at.isoformat(), "until": ec.last_active_at.isoformat(),
        "containers": [{"name": k, "count": v} for k, v in
                       sorted(entities.items(), key=lambda kv: -kv[1])[:50]],
        "hosts": affected_hosts(ec.sources, entities), "docker_hosts": sorted(ec.sources or []),
        "graylog_url": None,
    }


async def resend_pending(cfg) -> int:
    """Beschlossene, aber nicht zugestellte Mails der letzten 2 h nachholen (nach Neustart)."""
    from app.core.database import AsyncSessionLocal
    from app.core.tasks import run_background
    from app.models.error_cluster import ErrorCluster, LogTemplate
    from app.services.error_clusters.runner import short_title
    since = datetime.now(timezone.utc) - timedelta(hours=2)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ErrorCluster, LogTemplate)
            .join(LogTemplate, LogTemplate.key == ErrorCluster.key, isouter=True)
            .where(ErrorCluster.notify_class == "immediate", ErrorCluster.notified_at >= since,
                   ErrorCluster.mailed_at.is_(None)))).all()
    for ec, t in rows:
        ctx = ctx_from_row(ec, t)
        title = (f"Flotten-Ausfall: {ctx['template']}" if ec.source == "checkmk_fleet"
                 else f"Fehlerausbruch: {short_title(ctx['template'])}")
        log.info("error_clusters: resending undelivered notification %s", str(ec.id)[:8])
        run_background(deliver(ctx, title, str(ec.id),
                               str(ec.incident_id) if ec.incident_id else None, cfg),
                       name=f"error_cluster_resend_{str(ec.id)[:8]}")
    return len(rows)
