"""Betrieb der Fehler-Cluster-Erkennung: alle 5 min ein Fenster, Ergebnis in Postgres.

Der Store lebt im Backend-Prozess (engine.MemoryStore) und wird beim ersten Lauf aus
Postgres geladen; danach schreibt jeder Lauf nur, was er geändert hat. Pro Lauf die
14-Tage-Historie aller aktiven Muster neu zu laden wären alle 5 min Hunderttausende Zeilen.

Erster Start: keine Lernphase ohne Meldungen. Der Runner holt die letzten 24 h aus
Graylog nach — still, ohne Meldungen — und meldet danach sofort mit echter Grundlast.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.services.error_clusters import critical
from app.services.error_clusters.collector import bucket_floor, fetch_window, tally
from app.services.error_clusters.engine import (
    BUCKET, HISTORY, BucketRec, Episode, Event, MemoryStore, TemplateInfo, fresh_at_coverage,
    process,
)
from app.services.error_clusters.rules import DEFAULT
from app.services.error_clusters.miner import Miner

log = logging.getLogger(__name__)

#: Graylog braucht einen Moment, bis die Zeilen eines Fensters indexiert sind.
INGEST_LAG = timedelta(minutes=2)
#: So viel wird nach einer Pause (Neustart, Ausfall) nachgeholt — still, ohne Meldungen.
MAX_CATCHUP = timedelta(minutes=30)
#: Beim allerersten Start: so viel Historie wird still nachgeholt (statt 24 h Lernphase).
INITIAL_BACKFILL = timedelta(hours=24)
#: Ereignisse aus Fenstern, die älter sind, werden nicht gemeldet (Nachholen).
REPORT_HORIZON = timedelta(minutes=15)
ENTITY_HISTORY = timedelta(hours=48)


@dataclass
class _Runtime:
    store: MemoryStore
    miner: Miner
    last_bucket: datetime | None
    #: episode.id → incident_id / alert_id (Postgres-UUIDs)
    incident_ids: dict[str, str]
    alert_ids: dict[str, str]


_rt: _Runtime | None = None
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Laden ────────────────────────────────────────────────────────────────────

COVERAGE_PREFIX = "error_clusters.coverage_since."


async def coverage_starts(db) -> list[datetime]:
    """Beginn ALLER bisher eingeführten Log-Quellen (je collector.SOURCE_VERSION).

    Nicht nur die neueste: sonst vergisst die Erkennung, dass die Muster der vorigen
    Umstellung schon beim Einschalten liefen (13.10.: 3 Fehl-Mails, vor Versand gestoppt).
    """
    from app.models.settings import GlobalSetting
    rows = await db.execute(select(GlobalSetting.value_plain)
                            .where(GlobalSetting.key.like(COVERAGE_PREFIX + "%")))
    out = []
    for (v,) in rows:
        try:
            out.append(datetime.fromisoformat(v))
        except (TypeError, ValueError):
            continue
    return sorted(out)


async def _record_burst(db, b: datetime, n_new: int, t, notice: bool) -> None:
    """Quellenwechsel festhalten (Beginn der Beobachtung) und — einmal je Wechsel — im Feed
    benennen. Statt Dutzender Einzelmeldungen EIN Eintrag mit Begründung; auch ein echtes
    Großereignis bleibt so sichtbar (und meldet weiter über bekannte Muster: SPITZE/AUSBREITUNG)."""
    from app.models.alert import Alert
    from app.services.feed_index import index_items
    from app.services.settings import set_setting
    await set_setting(db, f"{COVERAGE_PREFIX}auto-{b:%Y%m%dT%H%M}", b.isoformat())
    if not notice:
        return
    new = [tt for k, tt in t.templates.items()]
    hosts = Counter()
    for tt in new:
        hosts.update(tt.entities)
    title = (f"Viele neue Fehlermuster auf einmal: {n_new} in 5 min — "
             f"vermutlich Umstellung eines Log-Versenders")
    body = "\n".join([
        f"{n_new} Muster tauchten im Fenster {b:%d.%m. %H:%M} UTC erstmals auf (Normalbetrieb "
        f"05.–12.10.: höchstens 46 je Fenster, Schwelle {DEFAULT.burst_new_templates}).",
        "Behandelt als Quellenwechsel: diese Muster melden 24 h lang weder NEU noch SPITZE noch "
        "AUSBREITUNG (keine Grundlast); laufende Schleifen darunter erscheinen in der Tagesübersicht.",
        "Falls es doch ein Großereignis ist: bekannte Muster melden weiter normal.",
        "", "Meiste neue Zeilen von: " + ", ".join(f"{h} ({n})" for h, n in hosts.most_common(8)),
    ])
    ext_id = f"eclburst:{b:%Y%m%dT%H%M}"
    meta = {"kind": "error_cluster_burst", "host": (hosts.most_common(1) or [("", 0)])[0][0],
            "new_patterns": n_new, "window": b.isoformat()}
    row = Alert(source="graylog", severity="medium", title=title[:512], body=body,
                external_id=ext_id, status="new", metadata_=meta)
    db.add(row)
    await db.flush()
    await index_items([{"id": str(row.id), "type": "alert", "source": "graylog", "severity": "medium",
                        "title": title, "body": body, "metadata": meta, "created_at": b.isoformat(),
                        "status": "new", "external_id": ext_id}])


async def _hydrate(db) -> _Runtime:
    from app.models.error_cluster import (
        EntityBucket, ErrorCluster, ErrorClusterState, LogTemplate, LogTemplateBucket,
        LogTemplateHourly,
    )
    now = _now()
    state = (await db.execute(select(ErrorClusterState).where(ErrorClusterState.id == 1))
             ).scalar_one_or_none()
    if state is None:
        started = bucket_floor(now - INITIAL_BACKFILL)
        state = ErrorClusterState(id=1, started_at=started, miner_state=None,
                                  last_bucket=started - BUCKET, updated_at=now)
        db.add(state)
        await db.commit()
        log.info("error_clusters: first start — backfilling %s silently", INITIAL_BACKFILL)

    # Seit wann die heutige Log-Quelle (collector.SOURCE_VERSION) gelesen wird — beim ersten
    # Start mit einer neuen Quelle jetzt. Muster, die gleich danach auftauchen, liefen
    # womöglich schon vorher (engine.COVERAGE_MARGIN).
    from app.services.error_clusters.collector import SOURCE_VERSION
    from app.services.settings import get_setting, set_setting
    cov_key = COVERAGE_PREFIX + SOURCE_VERSION
    cov_raw = await get_setting(db, cov_key)
    if cov_raw:
        coverage_since = datetime.fromisoformat(cov_raw)
    else:
        # Das erste Fenster, das mit der neuen Quelle gelesen wird — nicht die Uhrzeit: die
        # Erkennung läuft einige Minuten hinterher (13.10.: Uhr 11:47, erstes Fenster 11:35;
        # die neuen Muster lagen damit "vor" dem Beginn und galten als bekannt).
        coverage_since = state.last_bucket + BUCKET if state.last_bucket else now
        await set_setting(db, cov_key, coverage_since.isoformat())
        await db.commit()
        log.info("error_clusters: log source %s observed from %s", SOURCE_VERSION, coverage_since)
    store = MemoryStore(state.started_at, await coverage_starts(db))
    since = now - HISTORY - timedelta(hours=1)
    for t in (await db.execute(select(LogTemplate).where(LogTemplate.last_seen >= since))).scalars():
        store.templates[t.key] = TemplateInfo(t.key, t.template, t.first_seen, t.last_seen,
                                              bool(t.muted), list(t.examples or []))
    recent = now - BUCKET * 4
    rows = await db.execute(select(LogTemplateBucket.key, LogTemplateBucket.bucket_start,
                                   LogTemplateBucket.count, LogTemplateBucket.n_entities,
                                   LogTemplateBucket.entities)
                            .where(LogTemplateBucket.bucket_start >= since))
    for key, b, count, n_ent, ents in rows:
        store.buckets.setdefault(key, {})[b] = BucketRec(
            count, n_ent or 0, Counter(ents or {}) if b >= recent else None)
    from app.services.error_clusters.engine import HOURLY_HISTORY
    for key, h, count in await db.execute(
            select(LogTemplateHourly.key, LogTemplateHourly.hour_start, LogTemplateHourly.count)
            .where(LogTemplateHourly.hour_start >= now - HOURLY_HISTORY - timedelta(hours=1))):
        store.hourly.setdefault(key, {})[h] = count
    for ent, b, count in await db.execute(
            select(EntityBucket.entity, EntityBucket.bucket_start, EntityBucket.count)
            .where(EntityBucket.bucket_start >= now - ENTITY_HISTORY)):
        store.entity_buckets.setdefault(ent, {})[b] = count

    incident_ids: dict[str, str] = {}
    cool = now - timedelta(hours=6)
    # Nur Log-Episoden: Flotten-Episoden (source="checkmk_fleet") führt fleet.py selbst.
    # Geladen in den Log-Store suchte die Engine ihr Muster und brach mit KeyError ab.
    for ec in (await db.execute(select(ErrorCluster).where(
            ErrorCluster.source == "log",
            (ErrorCluster.status == "open") | (ErrorCluster.closed_at >= cool)))).scalars():
        ep = Episode(id=str(ec.id), key=ec.key, started_at=ec.started_at,
                     last_active_at=ec.last_active_at, rules=set(ec.rules or []),
                     notify_class=ec.notify_class, notified_rate=ec.notified_rate,
                     notified_entities=ec.notified_entities, notified_at=ec.notified_at,
                     peak15=ec.peak15, total=ec.total, entities=Counter(ec.entities or {}),
                     sources=set(ec.sources or []), closed_at=ec.closed_at,
                     reasons=list(ec.reasons or []))
        if ec.incident_id:
            incident_ids[ep.id] = str(ec.incident_id)
        if ec.status == "open":
            store.open[ep.key] = ep
        else:
            prev = store.last_closed.get(ep.key)
            if prev is None or (ep.closed_at and prev.closed_at and ep.closed_at > prev.closed_at):
                store.last_closed[ep.key] = ep
                store.closed.append(ep)
    miner = Miner(state.miner_state) if state.miner_state else Miner()
    log.info("error_clusters: hydrated %d templates, %d open episodes",
             len(store.templates), len(store.open))
    return _Runtime(store, miner, state.last_bucket, incident_ids, {})


# ── Speichern ────────────────────────────────────────────────────────────────

async def _persist(db, rt: _Runtime, t) -> None:
    from app.models.error_cluster import (
        EntityBucket, ErrorCluster, ErrorClusterState, LogTemplate, LogTemplateBucket,
    )
    store = rt.store
    for key in store.dirty_templates:
        info = store.templates[key]
        stmt = pg_insert(LogTemplate).values(
            key=key, template=info.template, examples=info.examples,
            first_seen=info.first_seen, last_seen=info.last_seen, muted=False)
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"template": info.template, "examples": info.examples,
                  "last_seen": info.last_seen}))
    rows = []
    for key, b in store.new_buckets:
        rec = store.buckets[key][b]
        rows.append({"key": key, "bucket_start": b, "count": rec.count,
                     "n_entities": rec.n_entities, "entities": dict(rec.entities or {}),
                     "complete": t.complete})
    if rows:
        await db.execute(pg_insert(LogTemplateBucket).values(rows).on_conflict_do_nothing())
    hourly = [{"key": k, "hour_start": h, "count": store.hourly[k][h]} for k, h in store.new_hourly]
    if hourly:
        from app.models.error_cluster import LogTemplateHourly
        stmt = pg_insert(LogTemplateHourly).values(hourly)
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["key", "hour_start"], set_={"count": stmt.excluded.count}))
    ents = [{"entity": e[:255], "bucket_start": t.bucket_start, "count": n}
            for e, n in t.entities.most_common(1000) if e]
    if ents:
        await db.execute(pg_insert(EntityBucket).values(ents).on_conflict_do_nothing())
    for ep in store.dirty_episodes.values():
        values = dict(
            id=uuid.UUID(ep.id), key=ep.key, source="log",
            status="closed" if ep.closed_at else "open", rules=sorted(ep.rules),
            reasons=list(ep.reasons),
            notify_class=ep.notify_class, started_at=ep.started_at,
            last_active_at=ep.last_active_at, closed_at=ep.closed_at, peak15=ep.peak15,
            total=ep.total, entities=dict(ep.entities.most_common(100)),
            sources=sorted(ep.sources), notified_rate=ep.notified_rate,
            notified_entities=ep.notified_entities, notified_at=ep.notified_at,
            incident_id=uuid.UUID(rt.incident_ids[ep.id]) if ep.id in rt.incident_ids else None,
        )
        upd = {k: v for k, v in values.items() if k != "id"}
        await db.execute(pg_insert(ErrorCluster).values(**values)
                         .on_conflict_do_update(index_elements=["id"], set_=upd))
    st = (await db.execute(select(ErrorClusterState).where(ErrorClusterState.id == 1))).scalar_one()
    st.miner_state = rt.miner.state()
    st.last_bucket = t.bucket_start
    st.updated_at = _now()


async def prune(db) -> None:
    """Täglich: alte Zählungen löschen (14 Tage 5-min, 28 Tage Stunden, 48 h Container)."""
    from app.models.error_cluster import EntityBucket, LogTemplateBucket
    now = _now()
    await db.execute(delete(LogTemplateBucket).where(
        LogTemplateBucket.bucket_start < now - HISTORY - timedelta(days=1)))
    await db.execute(delete(EntityBucket).where(EntityBucket.bucket_start < now - ENTITY_HISTORY))
    from app.models.error_cluster import LogTemplateHourly
    from app.services.error_clusters.engine import HOURLY_HISTORY
    await db.execute(delete(LogTemplateHourly).where(
        LogTemplateHourly.hour_start < now - HOURLY_HISTORY - timedelta(days=1)))
    await db.commit()


# ── Darstellung ──────────────────────────────────────────────────────────────

def short_title(template: str) -> str:
    """"ERROR - cue.zipline…changelog_reader.ChangelogReader - Failed to read …" →
    "ChangelogReader – Failed to read change-log: HTTP401 Unauthorized"."""
    parts = [p.strip() for p in template.replace("<*>", "…").split(" - ") if p.strip()]
    parts = [p for p in parts if p.upper() not in ("ERROR", "WARN", "WARNING", "CRITICAL",
                                                   "FATAL", "SEVERE", "EXCEPTION")]
    parts = [p.rsplit(".", 1)[-1] if " " not in p and "." in p else p for p in parts]
    text = " – ".join(parts) or template
    return text.strip(" '\"")[:140]


def graylog_link(base: str, template: str, since: datetime, until: datetime) -> str | None:
    from app.services.graylog_occurrences import iso, template_phrase
    phrase = template_phrase(template)
    if not base or not phrase:
        return None
    q = f'level:<=4 AND "{phrase}"'
    return (f"{base.rstrip('/')}/search?q={quote(q)}&rangetype=absolute"
            f"&from={quote(iso(since))}&to={quote(iso(until))}")


def affected_hosts(sources, entities) -> list[str]:
    """Docker-Hosts UND Container mit vollem Namen.

    CheckMK führt Container oft als eigene Hosts (piggyback): im Scope steht
    "cue-zipline-product-tz.cue.example.com", nicht der Docker-Host "cueservices". Nur
    die Docker-Hosts zu prüfen hätte den 401-Ausbruch an jedem vorbeigeleitet, dessen
    Filter die Zipline-Container enthält.
    """
    return sorted(set(sources or []) | {e for e in (entities or {}) if "." in e})


def entity_kind(entities, sources) -> str:
    """Was die Betroffenen sind: "Hosts", "Container" oder "Container und Hosts".

    Eine Zeile gehört zum Container, wenn sie einen trägt, sonst zur Quelle (collector.Line.
    entity) — ein Betroffener, der zugleich Quelle ist, ist also ein Host. Bis 03.11. hieß
    alles "Container": 13 RHEL-Server mit rhsmcertd-Fehlern standen in der Mail als
    "Betroffen (13 Container)".
    """
    srcs = {str(s).lower() for s in (sources or ())}
    names = [str(e).lower() for e in (entities or ())]
    hosts = sum(1 for n in names if n in srcs)
    if not names or hosts == len(names):
        return "Hosts"
    return "Container" if hosts == 0 else "Container und Hosts"


def cluster_context(ev: Event, base_url: str) -> dict:
    ep = ev.episode
    until = ev.bucket_start + BUCKET
    return {
        "cluster_id": ep.id,
        "template_key": ep.key,
        "template": ev.template.template,
        "examples": ev.template.examples[:5],
        "rules": sorted(ep.rules),
        "reasons": list(ev.verdict.reasons),
        "count15": ev.verdict.count15,
        "expected15": ev.verdict.expected15,
        "total": ep.total,
        "peak15": ep.peak15,
        "started_at": ep.started_at.isoformat(),
        "until": until.isoformat(),
        "containers": [{"name": n, "count": c} for n, c in ep.entities.most_common(50)],
        "hosts": affected_hosts(ep.sources, ep.entities),
        "docker_hosts": sorted(ep.sources),
        "entity_kind": entity_kind(ep.entities, ep.sources),
        "graylog_url": graylog_link(base_url, ev.template.template, ep.started_at, until),
    }


# ── Ereignisse → Feed + Incident ─────────────────────────────────────────────

async def _regeln_uebernehmen(art: str) -> bool:
    """Entscheidet das Regelwerk für diese Incident-Art (aktive Incident-Regel)? Dann eröffnet und meldet
    der alte Weg nicht mehr — seine Ereignisse speisen weiter das Raster (signal:neu/anstieg/…)."""
    from app.services.log_watch.incident_regeln import regeln_uebernehmen
    return await regeln_uebernehmen(art)


async def _handle(db, rt: _Runtime, events: list[Event], base_url: str) -> None:
    from app.models.alert import Alert
    from app.services.error_clusters.incident import close_cluster_incident, open_cluster_incident
    from app.services.feed_index import index_items, update_metadata

    docs = []
    for ev in events:
        ep = ev.episode
        ctx = cluster_context(ev, base_url)
        n_cont = len(ep.entities)
        severity = ("critical" if {"spike", "spread"} <= ep.rules else
                    "high" if ep.notify_class == "immediate" else "medium")
        title = f"Fehlerausbruch: {short_title(ev.template.template)} · {n_cont} {ctx['entity_kind']}" \
            if ep.notify_class == "immediate" else f"Neues Fehlermuster: {short_title(ev.template.template)}"
        meta = {
            "kind": "error_cluster", "cluster_id": ep.id, "host": (ctx["docker_hosts"] or ctx["hosts"] or [""])[0],
            "host_candidates": ctx["hosts"], "rules": ctx["rules"],
            # Gleiches Format wie die Graylog-Zählung → dieselbe Anzeige im Feed
            # ("1 334× in 60 min · 22 Container").
            "occurrences": {"count": ep.total, "since": ctx["started_at"], "until": ctx["until"],
                            "containers": ctx["containers"]},
            "external_url": ctx["graylog_url"],
        }
        ext_id = f"ecl:{ep.id}"
        alert_id = rt.alert_ids.get(ep.id)
        if alert_id is None:
            row = (await db.execute(select(Alert).where(Alert.external_id == ext_id))).scalar_one_or_none()
            alert_id = str(row.id) if row else None
        if alert_id is None and ev.action in ("open", "open_digest", "reopen", "renotify"):
            body = "\n".join(ctx["reasons"] + ["", "Beispiel:", *(ctx["examples"][:3])])
            row = Alert(source="graylog", severity=severity, title=title[:512], body=body,
                        external_id=ext_id, status="new", metadata_=meta)
            db.add(row)
            await db.flush()
            alert_id = str(row.id)
            docs.append({"id": alert_id, "type": "alert", "source": "graylog",
                         "severity": severity, "title": title, "body": body, "metadata": meta,
                         "created_at": ep.started_at.isoformat(), "status": "new",
                         "external_url": ctx["graylog_url"], "external_id": ext_id})
        elif alert_id is not None:
            await update_metadata("graylog", alert_id, meta)
        if alert_id:
            rt.alert_ids[ep.id] = alert_id

        if ep.group_id is not None and ev.action in ("open", "reopen", "renotify"):
            leader_inc = rt.incident_ids.get(ep.group_id)
            if leader_inc:
                from app.services.error_clusters.incident import add_pattern
                await add_pattern(db, leader_inc, {
                    "template_key": ep.key, "template": ev.template.template,
                    "count15": ev.verdict.count15, "total": ep.total,
                    "rules": sorted(ep.rules), "started_at": ep.started_at.isoformat(),
                    "containers": ctx["containers"][:15], "hosts": ctx["hosts"],
                    "examples": ctx["examples"][:2],
                }, [{"external_id": ext_id, "source": "graylog"}])
                rt.incident_ids[ep.id] = leader_inc
            continue
        if (ev.mail or (ev.action == "reopen" and ep.id in rt.incident_ids)) and not await _regeln_uebernehmen("log_cluster"):
            inc = await open_cluster_incident(
                db, kind="log_cluster", title=title, severity=severity,
                primary_host=(ctx["docker_hosts"] or ctx["hosts"] or [""])[0], context=ctx,
                member_docs=[{"external_id": ext_id, "source": "graylog"}],
                existing_id=rt.incident_ids.get(ep.id))
            rt.incident_ids[ep.id] = str(inc.id)
        elif ev.action == "close" and ep.id in rt.incident_ids:
            # Erst schließen, wenn ALLE Muster des Ausbruchs ruhig sind — nicht schon,
            # wenn das erste endet, während ein zugehöriges noch läuft.
            group = ep.group_id or ep.id
            still = [o for o in rt.store.open.values()
                     if o.id != ep.id and (o.id == group or o.group_id == group)]
            if not still:
                await close_cluster_incident(db, rt.incident_ids[ep.id],
                                             "alle Muster seit 60 min unter der doppelten Grundlast")
    if docs:
        await index_items(docs)


# ── Lauf ─────────────────────────────────────────────────────────────────────

async def run_error_cluster_detection() -> None:
    """Scheduler-Job. APScheduler protokolliert hier nichts — eine Ausnahme verschwände
    sonst still, und die Erkennung stünde, ohne dass es jemand merkt."""
    try:
        await _run()
    except Exception:
        log.exception("error_clusters: run failed")


async def _run() -> None:
    """Abgeschlossene Fenster seit dem letzten Lauf verarbeiten."""
    global _rt
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_error_cluster_config

    async with AsyncSessionLocal() as db:
        cfg = await get_error_cluster_config(db)
        if not cfg.enabled:
            return
        svc, base_url = await _graylog(db)
    if svc is None:
        return
    if _lock.locked():
        return
    async with _lock:
        if _rt is None:
            async with AsyncSessionLocal() as db:
                _rt = await _hydrate(db)
            # Neustart: was beschlossen, aber nicht zugestellt war, jetzt zustellen.
            from app.services.error_clusters import notify as _notify
            await _notify.resend_pending(cfg)
        rt = _rt
        now = _now()
        target = bucket_floor(now - INGEST_LAG) - BUCKET          # letztes fertiges Fenster
        b = (rt.last_bucket + BUCKET) if rt.last_bucket else target
        # Erstes Nachholen (24 h ab Erkennungsstart) darf lang sein; danach wird eine
        # Lücke (Ausfall) nur bis MAX_CATCHUP nachgeholt — ältere Fenster fehlen dann.
        backfilling = b - rt.store.detector_started_at < INITIAL_BACKFILL
        allowed = INITIAL_BACKFILL + timedelta(hours=1) if backfilling else MAX_CATCHUP
        if target - b > allowed:
            log.warning("error_clusters: gap %s — skipping to %s", target - b, target - allowed)
            b = target - allowed
        if b > target:
            return
        n_windows = int((target - b) / BUCKET) + 1
        log.info("error_clusters: %d window(s) %s … %s%s", n_windows, b, target,
                 " (backfill, silent)" if n_windows > 3 else "")
        t_start = _now()
        n_events = n_mails = 0
        while b <= target:
            try:
                fetched = await fetch_window(svc, b, b + BUCKET)
            except Exception as exc:
                log.warning("error_clusters: Graylog fetch %s failed: %s", b, exc)
                return
            from app.services.log_watch.vokabular import themen_ids
            t = await asyncio.to_thread(tally, fetched.lines, rt.miner, b,
                                        fetched.total, fetched.complete, themen_ids)
            if not fetched.complete:
                await _recount_exact(svc, t, b)
            # Massen-Neuheit = Quellenwechsel: so viele neue Muster in EINEM Fenster gab es
            # im Normalbetrieb nie (max. 46) — ein Versender wurde umgestellt. Ab hier gelten
            # Muster dieses Fensters als "schon vorher gelaufen" (engine.fresh_at_coverage).
            n_new = sum(1 for k in t.templates if k not in rt.store.templates)
            burst = n_new >= DEFAULT.burst_new_templates
            burst_known = burst and fresh_at_coverage(b, rt.store.coverage_starts, None)
            if burst:
                rt.store.coverage_starts = sorted({*rt.store.coverage_starts, b})
                log.warning("error_clusters: %d new patterns in window %s — treated as a "
                            "log-source change (coverage start)", n_new, b)
            events = process(rt.store, t)
            report = now - (b + BUCKET) <= REPORT_HORIZON
            if not report:
                # Still nachgeholt: nichts wurde gemeldet. Den Meldestand zurücksetzen,
                # sonst bliebe ein Ausbruch, der beim Nachholen begann und noch läuft,
                # stumm — die Engine hielte ihn für bereits gemeldet.
                for ev in events:
                    if ev.mail:
                        ev.episode.notified_rate = 0
                        ev.episode.notified_entities = 0
                        ev.episode.notified_at = None
            found = critical.scan(fetched.lines)
            if report:
                # Raster der Log-Überwachung: dieselben Zeilen, kein zweiter Abruf.
                # graylog_fenster schluckt eigene Fehler — die Erkennung läuft weiter.
                from app.services.log_watch import lauf as raster_lauf
                stumm = {k for k, ti in rt.store.templates.items() if ti.muted}
                await raster_lauf.graylog_fenster(t.templates, events, found, stumm, b)
            crit: list[dict] = []
            async with AsyncSessionLocal() as db:
                await _persist(db, rt, t)
                if report and events:
                    await _handle(db, rt, events, base_url)
                    # Im selben Commit sind die Episoden mit ihren Incident-IDs zu sichern.
                    for ev in events:
                        rt.store.dirty_episodes[ev.episode.id] = ev.episode
                    await _persist_episodes_only(db, rt)
                if burst:
                    await _record_burst(db, b, n_new, t, notice=report and not burst_known)
                # Kritische Kernelereignisse (OOM-Kill …): eigener Savepoint — ein Fehler
                # dort darf die Mengen-Erkennung dieses Fensters nicht mitreißen.
                try:
                    async with db.begin_nested():
                        crit = await critical.process(db, found, b, report, base_url)
                except Exception:
                    log.exception("error_clusters: critical events of window %s failed", b)
                    crit = []
                await db.commit()
            if report and events:
                from app.services.error_clusters import notify
                await notify.on_events(_hold_young(rt, events, now), rt, cfg, base_url)
            if crit:
                from app.services.error_clusters import notify
                await notify.on_critical(crit, cfg)
            rt.last_bucket = b
            n_events += len(events) if report else 0
            n_mails += sum(1 for e in events if e.mail) if report else 0
            b += BUCKET
        # Zurückgehaltene Meldungen junger Ausbrüche: jetzt alt genug und noch aktiv?
        due = _release_held(rt, now)
        if due:
            from app.services.error_clusters import notify
            await notify.on_events(due, rt, cfg, base_url)
            n_mails += len(due)
        # CheckMK-Flotten-Cluster: einmal je Lauf, nur im Echtbetrieb (nicht beim Nachholen).
        if now - target <= REPORT_HORIZON:
            try:
                from app.services.error_clusters import fleet, notify
                async with AsyncSessionLocal() as db:
                    fleet_items = await fleet.run(db, cfg, base_url)
                    await db.commit()
                await notify.on_fleet(fleet_items, cfg)
                n_mails += len(fleet_items)
            except Exception:
                log.exception("error_clusters: fleet detection failed")
        log.info("error_clusters: done in %.0fs — %d templates, %d open episodes, "
                 "%d event(s), %d mail event(s)", (_now() - t_start).total_seconds(),
                 len(rt.store.templates), len(rt.store.open), n_events, n_mails)


# ── Mindestalter für Mails (rules.min_age_minutes) ───────────────────────────
#: Zurückgehaltene Mail-Ereignisse je Episode. Im Speicher: nach einem Neustart greift
#: notify.resend_pending — mit derselben Altersregel.
_held: dict[str, Any] = {}
HELD_ACTIVE_WINDOW = timedelta(minutes=15)


def _young(ep, now) -> bool:
    from app.services.error_clusters.rules import DEFAULT
    return now - ep.started_at < timedelta(minutes=DEFAULT.min_age_minutes)


def _hold_young(rt, events, now):
    """Mails für Ausbrüche, die jünger als min_age sind, zurückhalten (nicht verwerfen)."""
    from app.services.error_clusters.rules import DEFAULT
    out = []
    for ev in events:
        # "anhaltend neu" läuft nachweislich schon 2 h — kein weiteres Zurückhalten.
        persistent = "persistent" in (getattr(getattr(ev, "verdict", None), "rules", None) or ())
        if ev.mail and ev.action in ("open", "reopen") and not persistent and _young(ev.episode, now):
            _held[ev.episode.id] = ev
            log.info("error_clusters: mail for %s held until it is %d min old (pattern %s)",
                     ev.episode.id[:8], DEFAULT.min_age_minutes, ev.episode.key[:8])
            ev = _without_mail(ev)
        out.append(ev)
    return out


def _without_mail(ev):
    import dataclasses
    return dataclasses.replace(ev, mail=False) if dataclasses.is_dataclass(ev) else ev


def _release_held(rt, now) -> list:
    """Alt genug und noch aktiv → melden; vorher vorbei → verwerfen (war nur kurz)."""
    from app.services.error_clusters.rules import DEFAULT
    due = []
    for eid, ev in list(_held.items()):
        ep = ev.episode
        if ep.closed_at is not None or (rt.store.open.get(ep.key) is not ep):
            log.info("error_clusters: held mail for %s dropped — outbreak ended before %d min",
                     eid[:8], DEFAULT.min_age_minutes)
            _held.pop(eid)
            continue
        if _young(ep, now):
            continue
        _held.pop(eid)
        if now - ep.last_active_at <= HELD_ACTIVE_WINDOW:
            due.append(ev)
        else:
            log.info("error_clusters: held mail for %s dropped — no longer active", eid[:8])
    return due


#: So viele der größten Muster eines gekappten Fensters zählt Graylog exakt nach.
RECOUNT_TOP = 10


async def _recount_exact(svc, t, b: datetime) -> None:
    """Gekapptes Fenster (> 80 000 Zeilen in 5 min): die größten Muster exakt zählen.

    Die Stichprobe unterschätzt genau die Muster, um die es bei einer Flut geht. Graylog
    zählt serverseitig (Pivot nach Container) über die feste Phrase des Musters; der
    größere der beiden Werte gilt, die Container-Aufteilung kommt aus der Zählung.
    """
    from app.services.error_clusters.collector import BASE_QUERY
    from app.services.graylog_occurrences import _quote, iso, template_phrase
    specs = []
    for key, tt in sorted(t.templates.items(), key=lambda kv: -kv[1].count)[:RECOUNT_TOP]:
        phrase = template_phrase(tt.template)
        if phrase:
            q = f"({BASE_QUERY}) AND message:{_quote(phrase)}"
            specs.append({"id": key, "query": q, "since": iso(b), "until": iso(b + BUCKET)})
    if not specs:
        return
    try:
        counts = await svc.count_occurrences(specs)
    except Exception as exc:
        log.warning("error_clusters: exact recount failed: %s", exc)
        return
    for key, c in counts.items():
        tt = t.templates[key]
        if c["count"] > tt.count:
            tt.count = c["count"]
            if c.get("containers"):
                tt.entities = Counter({x["name"]: x["count"] for x in c["containers"]})
    log.info("error_clusters: window %s capped — %d pattern(s) recounted exactly", b, len(counts))


async def _persist_episodes_only(db, rt: _Runtime) -> None:
    from app.models.error_cluster import ErrorCluster
    from sqlalchemy import update
    for ep in rt.store.dirty_episodes.values():
        if ep.id in rt.incident_ids:
            await db.execute(update(ErrorCluster).where(ErrorCluster.id == uuid.UUID(ep.id))
                             .values(incident_id=uuid.UUID(rt.incident_ids[ep.id])))


async def _graylog(db):
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig
    from app.services.connectors.graylog import GraylogConnector
    c = (await db.execute(select(ConnectorConfig).where(
        ConnectorConfig.type == "graylog", ConnectorConfig.enabled.is_(True)))).scalars().first()
    if c is None:
        return None, ""
    return (GraylogConnector(base_url=c.base_url, credentials=decrypt_credentials(c.encrypted_credentials)),
            c.base_url or "")


def pattern_key(message: str) -> str | None:
    """Schlüssel des Musters einer Meldung — nur nachschlagen, nichts lernen.

    Für den Feed: Graylog-Einträge tragen so das Muster, zu dem ihr Cluster gehört.
    None, solange die Erkennung im Prozess noch nicht geladen ist.
    """
    if _rt is None:
        return None
    try:
        mined = _rt.miner.match(message)
    except Exception:
        return None
    return mined.key if mined else None
