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
    from app.services.log_watch.incident_regeln import lesbar
    if ctx.get("regel"):
        # Incident aus einer Regel des Regelwerks: Name der Regel, was, wie viele — ohne Platzhalter
        head = "KI-Einschätzung" if followup else "Incident"
        return f"[CentralStation] {head}: {lesbar(title)}"
    n_cont = len(ctx.get("containers") or [])
    if ctx.get("kind") == "critical":
        # Kritisches Ereignis (critical.py): Art, Host, Wiederholung — keine Zeilenrate.
        head = "KI-Einschätzung" if followup else "Kritisches Ereignis"
        host = (ctx.get("hosts") or ["?"])[0]
        return (f"[CentralStation] {head}: {ctx.get('label')} auf {host} · "
                f"{ctx.get('events')}× in {ctx.get('window_hours')} h")
    head = "KI-Einschätzung" if followup else "Fehlerausbruch"
    # Drain-Platzhalter (<NUM> <TS> <IP> …) tilgen — gemessen 07.11.: der Betreff war unlesbar
    short = lesbar(title.split(": ", 1)[-1].split(" · ")[0])
    return (f"[CentralStation] {head}: {short} · {n_cont} {ctx.get('entity_kind') or 'Container'} · "
            f"{fmt_rate(ctx.get('count15'), 900)}")


def render_html(ctx: dict, triage: dict | None, incident_url: str | None,
                pending: bool) -> str:
    """Deutsche Mail. Inhalt vor Chrom: KI-Einschätzung, Muster, Verlauf, Betroffene."""
    rows = []
    if incident_url:
        rows.append(f'<p style="margin:0 0 10px"><a href="{_esc(incident_url)}"><b>→ Incident öffnen</b></a></p>')
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

    rows.append(_research_html(ctx))

    containers = ctx.get("containers") or []
    cont_html = "".join(f"<tr><td>{_esc(c['name'])}</td><td style='text-align:right'>"
                        f"{fmt_int(c['count'])}</td></tr>" for c in containers[:15])
    more = f"<p style='color:#777'>… und {len(containers) - 15} weitere</p>" if len(containers) > 15 else ""
    examples = "".join(f"<pre style='white-space:pre-wrap;background:#f4f4f4;padding:6px;"
                       f"margin:4px 0;font-size:12px'>{_esc(e)}</pre>"
                       for e in (ctx.get("examples") or [])[:3])
    reasons = "".join(f"<li>{_esc(r)}</li>" for r in ctx.get("reasons") or [])
    critical_kind = ctx.get("kind") == "critical"
    if ctx.get("regel"):
        volume = (f'<p style="margin:4px 0"><b>{fmt_int(ctx.get("nachrichten"))} relevante Meldungen</b> · '
                  f'{len(ctx.get("hosts") or [])} Hosts · {fmt_int(ctx.get("quellen"))} Quellen in '
                  f'{_esc(ctx.get("fenster_min"))} min — Regel {_esc(ctx.get("regel"))} '
                  f'„{_esc(ctx.get("regel_name"))}“, Filter <code>{_esc(ctx.get("regel_filter"))}</code></p>')
    elif critical_kind:
        volume = (f'<p style="margin:4px 0">Erstes Ereignis {_esc(_hm(ctx.get("started_at")))} UTC, '
                  f'zuletzt {_esc(_hm(ctx.get("until")))} UTC · <b>{fmt_int(ctx.get("events"))}×</b> '
                  f'in {_esc(ctx.get("window_hours"))} h (je Ereignis ein 5-min-Fenster)</p>')
    else:
        volume = (f'<p style="margin:4px 0">Seit {_esc(_hm(ctx.get("started_at")))} UTC · '
                  f'{fmt_int(ctx.get("total"))} Zeilen gesamt · zuletzt '
                  f'<b>{fmt_rate(ctx.get("count15"), 900)}</b> ({fmt_int(ctx.get("count15"))} in 15 min, '
                  f'erwartet {fmt_rate(ctx.get("expected15") or 0, 900)})</p>')
    links = []
    if incident_url:
        links.append(f'<a href="{_esc(incident_url)}">Incident öffnen (Timeline, An Konsole übergeben)</a>')
    if ctx.get("graylog_url"):
        links.append(f'<a href="{_esc(ctx["graylog_url"])}">In Graylog ansehen</a>')
    if incident_url and "/feed?incident=" in incident_url:
        # der Incident als Objekt: seine Nachrichten im Fehler-Netzplan (incident_objekt.py)
        base, _, iid = incident_url.partition("/feed?incident=")
        links.append(f'<a href="{_esc(base)}/topology?modus=fehler&amp;filter=incident:{_esc(iid)}">Im Fehler-Netzplan</a>')
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;max-width:760px">
    {''.join(rows)}
    <h4 style="margin:16px 0 4px">Warum gemeldet</h4><ul>{reasons}</ul>
    <h4 style="margin:12px 0 4px">Muster</h4>
    <pre style="white-space:pre-wrap;background:#f4f4f4;padding:6px;font-size:12px">{_esc(ctx.get('template'))}</pre>
    {volume}
    <h4 style="margin:12px 0 4px">Betroffen ({len(containers)} {'Host' if critical_kind else (ctx.get('entity_kind') or 'Container')})</h4>
    <table style="border-collapse:collapse;font-size:13px">{cont_html}</table>{more}
    <h4 style="margin:12px 0 4px">Beispiele</h4>{examples}
    <p style="margin:14px 0">{' · '.join(links)}</p>
    <p style="color:#888;font-size:12px;margin-top:18px">Automatisch erkannt – keine Aktion ausgeführt.
       Abbestellen: Einstellungen → E-Mail-Berichte.</p></div>"""


def _without_host(title: str | None, host: str | None) -> str:
    """CheckMK-Titel lauten "host — Dienst"; der Host steht in der Zeile schon davor."""
    title = title or ""
    if host and title.startswith(host):
        return title[len(host):].lstrip(" —-:") or title
    return title


from app.services.error_clusters.engine import SIMILAR_MIN_SHARE as SIMILAR_SHARE  # noqa: E402


def _research_html(ctx: dict) -> str:
    """Was die automatische Suche fand — die Daten, auf denen die KI-Einschätzung beruht.

    Auch "nichts gefunden" steht da: ein leerer Abschnitt ist ein Befund, kein Fehlen.
    """
    corr = ctx.get("correlated")
    cmk = [a for a in (ctx.get("checkmk") or []) if a.get("titel")]
    parts = []
    if corr is not None:
        if corr:
            items = "".join(
                f"<li><code>{_esc(c.get('template', '')[:160])}</code><br>"
                f"<span style='color:#555'>{_esc(', '.join(x['name'] for x in c.get('containers') or []))}"
                f" — {fmt_int(c.get('zeilen_15min'))} Zeilen in 15 min ({_esc(c.get('faktor'))}× üblich)"
                f" · {round(100 * (c.get('similarity') or 0))} % gleiche Wörter</span></li>"
                for c in corr[:8])
            parts.append(f"<p style='margin:6px 0 2px'><b>Ähnliche Meldungen</b> "
                         f"<span style='color:#777'>(anderes Muster, ≥ {round(100 * SIMILAR_SHARE)} % "
                         f"gleiche Wörter, im selben 15-min-Fenster aktiv)</span>:</p>"
                         f"<ul style='margin:0'>{items}</ul>")
        else:
            parts.append("<p style='margin:6px 0 2px'><b>Ähnliche Meldungen:</b> "
                         "keine — die Meldung tritt nur in diesem Muster auf.</p>")
    if "checkmk" in ctx:
        hidden = ctx.get("checkmk_hidden") or 0
        hidden_txt = (f" {hidden} gleichzeitige Alert(s) auf einzelnen Hosts ohne belegten "
                      f"Zusammenhang ausgeblendet." if hidden else "")
        if cmk:
            items = "".join(
                f"<li>{_esc(a.get('dienst') or _without_host(a.get('titel'), a.get('host')))} "
                f"<span style='color:#777'>— {_esc(a.get('grund') or '')}</span></li>"
                for a in cmk[:10])
            parts.append(f"<p style='margin:6px 0 2px'><b>CheckMK-Alerts mit Zusammenhang</b> "
                         f"<span style='color:#777'>(signifikant häufiger auf den betroffenen Hosts "
                         f"als auf allen übrigen, gleiches Zeitfenster)</span>:</p>"
                         f"<ul style='margin:0'>{items}</ul>"
                         + (f"<p style='margin:2px 0;color:#777'>{hidden_txt}</p>" if hidden else ""))
        else:
            parts.append("<p style='margin:6px 0 2px'><b>CheckMK-Alerts mit Zusammenhang:</b> "
                         f"keine.{hidden_txt}</p>")
    if not parts:
        return ""
    return ("<div style='margin:12px 0;padding:8px;border:1px solid #ccc;border-radius:6px'>"
            "<b>Recherche</b> <span style='color:#777;font-size:12px'>(automatisch, nur lesend — "
            "Grundlage der KI-Einschätzung)</span>" + "".join(parts) + "</div>")


def _hm(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        return str(iso or "")


#: Grund der Empfängerwahl — steht im Zustellvermerk am Incident
IM_SCOPE, OHNE_SCOPE = "host-scope", "kein Scope trifft — alle Abonnenten"


def waehle_empfaenger(abonnenten: list[tuple[str, list[str]]], hosts: list[str]) -> tuple[list[str], str]:
    """(E-Mail, Host-Scope) der Abonnenten → (Empfänger, Grund). Wer den Incident im Scope hat (leerer Scope =
    alles), bekommt ihn; trifft niemandes Scope, bekommen ihn alle Abonnenten (Nutzer 07.11.2026 — vorher ging
    so ein Incident an niemanden, z. B. cue-Flotte, mssql06, web01). Rein — testbar."""
    from app.services.error_clusters.incident import host_matches
    treffer = sorted({m for m, scope in abonnenten if not scope or any(host_matches(x, hosts) for x in scope)})
    if treffer:
        return treffer, IM_SCOPE
    return sorted({m for m, _ in abonnenten}), OHNE_SCOPE


async def empfaenger(db, hosts: list[str]) -> tuple[list[str], str]:
    """Abonnenten der Sofort-Mail mit ihrem Host-Scope → waehle_empfaenger."""
    from app.models.user import User
    from app.models.workflow import UserPreference
    from app.services.feed_index import get_user_checkmk_host_scope

    rows = await db.execute(select(User, UserPreference)
                            .join(UserPreference, UserPreference.user_id == User.id))
    abonnenten = []
    for user, prefs in rows.all():
        ns = prefs.notification_settings or {}
        if ns.get("error_cluster_immediate") and getattr(user, "is_active", True) and user.email:
            abonnenten.append((user.email, await get_user_checkmk_host_scope(db, str(user.id))))
    return waehle_empfaenger(abonnenten, hosts)


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


async def _correlated(rt, ev, limit: int = 8) -> list[dict]:
    """Ähnliche Meldungen im selben 15-min-Fenster (engine.similar_patterns)."""
    from app.services.error_clusters.engine import similar_patterns
    return similar_patterns(rt.store, ev.template.key, ev.bucket_start, limit)


def _entity_kind(entities, sources) -> str:
    from app.services.error_clusters.runner import entity_kind
    return entity_kind(entities, sources)


#: Ein CheckMK-Alert gehört zum Ausbruch, wenn er auf den betroffenen Hosts signifikant
#: häufiger auftritt als auf allen übrigen im selben Zeitfenster (einseitiger exakter
#: Fisher-Test). Idee wie Luo et al., KDD 2014 ("Correlating Events with Time Series for
#: Incident Diagnosis"): verglichen wird mit einer Zufallsstichprobe, nicht bloße
#: Gleichzeitigkeit — hier sind die übrigen Hosts die Vergleichsstichprobe.
RELATED_P = 0.01
RELATED_MIN_HOSTS = 2


def hypergeom_sf(a: int, population: int, marked: int, drawn: int) -> float:
    """P(X ≥ a) für X ~ Hypergeometrisch(N=population, K=marked, n=drawn)."""
    from math import comb
    if a <= 0:
        return 1.0
    total = comb(population, drawn)
    if total == 0:
        return 1.0
    hi = min(marked, drawn)
    return sum(comb(marked, k) * comb(population - marked, drawn - k)
               for k in range(a, hi + 1)) / total


def related_alerts(per_service: dict[str, dict], affected_n: int, population: int) -> tuple[list[dict], int]:
    """Welche Dienste gehören zum Ausbruch? per_service: {dienst: {"hosts": set, "affected": set,
    "first": alert-dict}}. Rückgabe: (zusammenhängende, Anzahl ausgeblendeter Alerts)."""
    related, hidden = [], 0
    for svc, d in per_service.items():
        a, k = len(d["affected"]), len(d["hosts"])
        if a == 0:
            continue
        p = hypergeom_sf(a, max(population, k, affected_n), k, affected_n)
        enough = a >= RELATED_MIN_HOSTS or affected_n == 1
        if p < RELATED_P and enough:
            others, rest = k - a, max(population - affected_n, 0)
            related.append({**d["first"], "dienst": svc, "betroffen": a, "von": affected_n,
                            "sonst": others, "sonst_von": rest, "p": p,
                            "grund": (f"auf {a} von {affected_n} betroffenen Hosts, sonst auf "
                                      f"{others} von {rest} Hosts (p = {p:.1g})")})
        else:
            hidden += a
    related.sort(key=lambda x: x["p"])
    return related, hidden


async def related_checkmk(db, hosts: list[str], since: datetime,
                          until: datetime | None = None) -> tuple[list[dict], int]:
    """CheckMK-Alerts mit BELEGTEM Zusammenhang zum Ausbruch (statt aller Alerts der Hosts).

    Bis 03.11. stand hier jeder Alert der betroffenen Hosts ab 15 min vor Beginn — ohne
    Ende des Fensters und ohne Bezug: "File group PrintPdfExport" auf 1 von 13 Hosts wurde
    so zum "verwandten Signal" eines rhsmcertd-Fehlers, und die KI schloss daraus auf
    fehlende Konfigurationsdateien.
    """
    from sqlalchemy import text
    from app.models.alert import Alert
    from app.services.error_clusters.incident import host_matches
    until = until or datetime.now(timezone.utc)
    rows = (await db.execute(select(Alert).where(
        Alert.source == "checkmk", Alert.created_at >= since, Alert.created_at <= until))).scalars().all()
    population = (await db.execute(text(
        "SELECT count(DISTINCT lower(metadata->>'host')) FROM alerts WHERE source='checkmk' "
        "AND created_at >= now() - interval '30 days'"))).scalar() or 0
    known = (await db.execute(text(
        "SELECT DISTINCT lower(metadata->>'host') FROM alerts WHERE source='checkmk' "
        "AND created_at >= now() - interval '30 days'"))).scalars().all()
    # Betroffene, die CheckMK überhaupt kennt — nur sie können einen Alert haben.
    affected_known = [h for h in hosts if any(host_matches(k, [h]) for k in known if k)]
    per_service: dict[str, dict] = {}
    for a in rows:
        meta = a.metadata_ or {}
        h, svc = (meta.get("host") or "").lower(), meta.get("service") or ""
        if not h or not svc:
            continue
        d = per_service.setdefault(svc, {"hosts": set(), "affected": set(), "first": None})
        d["hosts"].add(h)
        if host_matches(h, affected_known):
            d["affected"].add(h)
            if d["first"] is None:
                d["first"] = {"host": h, "titel": a.title, "schwere": a.severity,
                              "zeit": a.created_at.isoformat()}
    return related_alerts(per_service, len(affected_known), population)


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
        checkmk, checkmk_hidden = await related_checkmk(
            db, ctx["hosts"], ev.episode.started_at - timedelta(minutes=15))
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
    # Recherche für die Mail (notify._research_html): nur auf diesem Weg tatsächlich
    # gesucht — Flotten-Cluster und Nachholen nach Neustart zeigen den Abschnitt nicht.
    ctx["correlated"] = correlated
    ctx["checkmk"] = checkmk
    ctx["checkmk_hidden"] = checkmk_hidden
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
        to, wahl = await empfaenger(db, ctx.get("hosts") or []) if cfg.mail_enabled else ([], "Mail abgeschaltet")
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
        triage_zustand = "ok" if result else ("pending" if task else "none")
        if incident_id:
            await _vermerke_zustellung(db, incident_id, sent, to, triage_zustand, wahl)
        await db.commit()
    log.info("error_clusters: notify %s → %d mail(s), triage=%s", cluster_id[:8], sent, triage_zustand)

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
    from app.services.log_watch.incident_regeln import regeln_uebernehmen
    if await regeln_uebernehmen("log_cluster"):
        return                      # das Regelwerk meldet diese Art (log_watch/incident_regeln.py)
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
    sections = []
    for group, (head, intro, count_label, who_label) in DIGEST_SECTIONS.items():
        rows_g = [(ec, t) for ec, t in items if digest_group(ec) == group]
        if not rows_g:
            continue
        trs = "".join(
            f"<tr><td style='padding:4px 8px'>{_esc(short_title(t.template))}</td>"
            f"<td style='padding:4px 8px;white-space:nowrap'>{_esc(ec.started_at.strftime('%d.%m. %H:%M'))}</td>"
            f"<td style='padding:4px 8px;text-align:right'>{fmt_int(ec.total)}</td>"
            f"<td style='padding:4px 8px'>{_esc(', '.join(list((ec.entities or {}).keys())[:3]))}</td>"
            f"<td style='padding:4px 8px'>{_esc(digest_note(ec)[:160])}</td></tr>"
            for ec, t in rows_g[:40])
        more = '' if len(rows_g) <= 40 else ', die 40 größten gezeigt'
        sections.append(f"""
    <h3>{head}</h3>
    <p style="color:#555">{intro} {len(rows_g)} Einträge{more}.</p>
    <table style="border-collapse:collapse;font-size:13px">
      <tr style="background:#eee"><th style="padding:4px 8px;text-align:left">Muster</th>
      <th style="padding:4px 8px">Erstmals</th><th style="padding:4px 8px">{count_label}</th>
      <th style="padding:4px 8px;text-align:left">{who_label}</th><th style="padding:4px 8px;text-align:left">Hinweis</th></tr>
      {trs}</table>""")
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;max-width:900px">
    {''.join(sections)}
    <p style="color:#888;font-size:12px;margin-top:18px">Automatisch erkannt – keine Aktion ausgeführt.
       Abbestellen: Einstellungen → E-Mail-Berichte.</p></div>"""


#: Abschnitte der Tagesübersicht — je Art ein eigener Name (sonst stünden kritische
#: Kernelereignisse und geplante Jobs unter "Muster, die es 14 Tage nicht gab").
DIGEST_SECTIONS = {
    "critical": ("Kritische Kernelereignisse (letzte 24 Stunden)",
                 "OOM-Kill, I/O-, Dateisystem- oder Hardwarefehler — einzeln aufgetreten; ab 3× in "
                 "6 h auf demselben Host folgt ein Incident mit Mail.", "Ereignisse", "Host"),
    "log": ("Neue Fehlermuster (letzte 24 Stunden)",
            "Muster, die es 14 Tage nicht gab, aber ohne Ausbruch — zum Durchsehen, nicht zum "
            "sofortigen Handeln.", "Zeilen", "Container"),
    "recurring": ("Wiederkehrende Flotten-Ausfälle (vermutlich geplante Jobs)",
                  "Derselbe CheckMK-Service fällt täglich zur gleichen Uhrzeit auf vielen Hosts "
                  "aus — kein Incident. Eine CheckMK-Downtime blendet ihn ganz aus.",
                  "Alerts", "Hosts"),
}


def digest_group(ec) -> str:
    if ec.source == "critical":
        return "critical"
    if ec.source == "checkmk_fleet" and "recurring" in (ec.rules or []):
        return "recurring"
    return "log"


def digest_note(ec) -> str:
    if digest_group(ec) == "log":
        return (ec.triage or {}).get("zusammenfassung") or ""
    return (ec.reasons or [""])[-1]


async def on_fleet(items: list[dict], cfg) -> None:
    """Flotten-Cluster (fleet.run) über denselben Zustellweg melden."""
    from app.services.log_watch.incident_regeln import regeln_uebernehmen
    if await regeln_uebernehmen("fleet"):
        return                      # das Regelwerk meldet diese Art (log_watch/incident_regeln.py)
    from app.core.tasks import run_background
    for it in items:
        run_background(deliver(it["ctx"], it["title"], it["cluster_id"], it["incident_id"], cfg,
                               [], [{"host": h} for h in it["ctx"].get("hosts", [])][:15]),
                       name=f"error_cluster_fleet_{it['cluster_id'][:8]}")
    if items:
        from app.services.ai_agent.scheduler import run_generative_refresh
        run_background(run_generative_refresh(), name="error_cluster_generative_refresh")


async def on_critical(items: list[dict], cfg) -> None:
    """Kritische Ereignisse (critical.process) melden — mit den CheckMK-Alerts des Hosts."""
    from app.services.log_watch.incident_regeln import regeln_uebernehmen
    if await regeln_uebernehmen("critical"):
        return                      # das Regelwerk meldet diese Art (log_watch/incident_regeln.py)
    from app.core.database import AsyncSessionLocal
    from app.core.tasks import run_background
    for it in items:
        ctx = it["ctx"]
        try:
            async with AsyncSessionLocal() as db:
                since = datetime.fromisoformat(ctx["started_at"]) - timedelta(minutes=15)
                ctx["checkmk"], ctx["checkmk_hidden"] = await related_checkmk(db, ctx["hosts"], since)
        except Exception as exc:  # noqa: BLE001 — ohne Recherche geht die Meldung trotzdem
            log.warning("error_clusters: critical checkmk lookup failed: %s", exc)
        run_background(deliver(ctx, it["title"], it["cluster_id"], it["incident_id"], cfg,
                               None, ctx.get("checkmk") or []),
                       name=f"error_cluster_critical_{it['cluster_id'][:8]}")


def zustellung(alt: list | None, sent: int, to: list[str], triage: str, jetzt: datetime,
               wahl: str = "") -> list[dict]:
    """Zustell-Vermerke am Incident fortschreiben (höchstens 20). Rein — testbar."""
    return ((alt or []) + [{"zeit": jetzt.isoformat(), "mails": sent, "empfaenger": list(to),
                            "triage": triage, **({"empfaenger_wahl": wahl} if wahl else {})}])[-20:]


async def _vermerke_zustellung(db, incident_id: str, sent: int, to: list[str], triage: str, wahl: str = "") -> None:
    """Dauerhafter Nachweis am Incident, ob und an wen gemeldet wurde — vorher nur im Container-Log
    (geht beim Neustart verloren; Graylog verwirft INFO-Zeilen der Container, drop_container_noise)."""
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import cluster_context as _cc, set_cluster_context
    try:
        inc = (await db.execute(select(Incident).where(Incident.id == uuid.UUID(incident_id)))).scalar_one_or_none()
    except ValueError:
        return
    if inc is None:
        return
    c = {k: v for k, v in (_cc(inc) or {}).items() if k != "type"}
    c["zustellungen"] = zustellung(c.get("zustellungen"), sent, to, triage, datetime.now(timezone.utc), wahl)
    set_cluster_context(inc, c)


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
        "entity_kind": _entity_kind(entities, ec.sources),
        "graylog_url": None,
    }


#: ErrorCluster.source → Incident-Art des Regelwerks (dieselben Namen wie regeln_uebernehmen in runner/fleet/critical)
INCIDENT_ART = {"log": "log_cluster", "checkmk_fleet": "fleet", "critical": "critical"}


def nicht_nachholen(source: str, uebernommen: set[str]) -> bool:
    """Gehört die Meldung dieser Quelle dem Regelwerk? Dann holt resend_pending sie nicht nach. Rein."""
    return INCIDENT_ART.get(source) in uebernommen


async def resend_pending(cfg) -> int:
    """Beschlossene, aber nicht zugestellte Mails der letzten 2 h nachholen (nach Neustart)."""
    from app.core.database import AsyncSessionLocal
    from app.core.tasks import run_background
    from app.models.error_cluster import ErrorCluster, LogTemplate
    from app.services.error_clusters.runner import short_title
    from app.services.error_clusters.rules import DEFAULT
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=2)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ErrorCluster, LogTemplate)
            .join(LogTemplate, LogTemplate.key == ErrorCluster.key, isouter=True)
            .where(ErrorCluster.notify_class == "immediate", ErrorCluster.notified_at >= since,
                   ErrorCluster.mailed_at.is_(None),
                   # Dieselbe Altersregel wie im laufenden Betrieb (rules.min_age_minutes):
                   # nur Ausbrüche, die alt genug UND noch offen/aktiv sind.
                   ErrorCluster.status == "open",
                   ErrorCluster.started_at <= now - timedelta(minutes=DEFAULT.min_age_minutes),
                   ErrorCluster.last_active_at >= now - timedelta(minutes=15)))).all()
        from app.services.error_clusters.runner import coverage_starts
        starts = await coverage_starts(db)
    # Arten, die das Regelwerk meldet (log_watch/incident_regeln.py), holt der alte Weg nicht nach: dort eröffnet
    # _handle kein Incident und on_events meldet nicht — mailed_at bleibt leer, und vor dem 09.11. ging so jede
    # Episode nach JEDEM Neustart als Mail OHNE Incident-Link raus (gemessen: 22 Mails 07.–09.11., gebündelt
    # 2–6 min nach den Backend-Neustarts, z. B. 11 Mails 09:50–09:53 nach dem Neustart 09:48).
    from app.services.log_watch.incident_regeln import regeln_uebernehmen
    uebernommen = {a for a in set(INCIDENT_ART.values()) if await regeln_uebernehmen(a)}
    sent = 0
    for ec, t in rows:
        if nicht_nachholen(ec.source, uebernommen):
            log.info("error_clusters: not resending %s — incident rules own %s", str(ec.id)[:8],
                     INCIDENT_ART.get(ec.source, ec.source))
            continue
        if from_fresh_source(ec, t, starts, now):
            log.info("error_clusters: not resending %s — pattern from a freshly read log source",
                     str(ec.id)[:8])
            continue
        sent += 1
        ctx = ctx_from_row(ec, t)
        title = (f"Flotten-Ausfall: {ctx['template']}" if ec.source == "checkmk_fleet"
                 else f"Fehlerausbruch: {short_title(ctx['template'])}")
        log.info("error_clusters: resending undelivered notification %s", str(ec.id)[:8])
        run_background(deliver(ctx, title, str(ec.id),
                               str(ec.incident_id) if ec.incident_id else None, cfg),
                       name=f"error_cluster_resend_{str(ec.id)[:8]}")
    return sent


def from_fresh_source(ec, t, coverage_starts, now: datetime) -> bool:
    """Log-Muster, das in den ersten 2 h einer neu gelesenen Quelle auftauchte.

    Dafür gibt es heute keine Sofort-Meldung (rules.evaluate, coverage_unsure) — also auch
    keine nachgeholte. Anlass 13.10.: 7 Fehlalarme der Filebeat-Umstellung waren vor der
    Regelkorrektur als "sofort" beschlossen; ein Neustart hätte sie nachträglich verschickt.
    """
    from app.services.error_clusters.engine import fresh_at_coverage
    return (ec.source == "log" and t is not None
            and fresh_at_coverage(t.first_seen, coverage_starts, now))
