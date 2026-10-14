"""Kritische Ereignisse: schwere Kernelmeldungen, die schon einzeln zählen.

Die Fehlererkennung (engine/rules) meldet nach MENGE — neu ≥ 20 Zeilen, Spitze ≥ 200,
anhaltend ≥ 100 in 2 h. Ein OOM-Kill sind 1–3 Kernelzeilen auf einem Host und ging darin
unter: docker0209 hatte 25+ Stunden lang jede Stunde einen OOM (Paperless/celeryd), ohne
dass irgendetwas gemeldet wurde (14.10.2026).

Hier zählt die ART, nicht die Menge — eine feste Liste von Signaturen (kein LLM):
  - erstes Auftreten je Host und Art → Episode in der Tagesübersicht;
  - ≥ critical_repeat_count Ereignisse binnen critical_repeat_hours → Incident + Mail;
  - critical_quiet_hours ohne Ereignis → Episode und Incident schließen.
Ein Ereignis = Host × Art × 5-min-Fenster (ein OOM schreibt mehrere Zeilen, zählt einmal).

Nur Kernelzeilen (application_name/facility "kernel" oder "kernel:" im Text), keine
Container: "grep 'invoked oom-killer' kern.log" im sudo-Protokoll ist kein OOM.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.services.error_clusters.rules import DEFAULT, Thresholds

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signature:
    id: str
    label: str
    pattern: re.Pattern
    severity: str          # für den Incident
    phrase: str            # feste Phrase für den Graylog-Link


SIGNATURES = (
    Signature("oom_kill", "OOM-Kill (Arbeitsspeicher erschöpft)",
              re.compile(r"Out of memory: Killed process|invoked oom-killer|"
                         r"Memory cgroup out of memory|oom-kill:constraint"), "high", "oom-kill"),
    Signature("kernel_panic", "Kernel panic", re.compile(r"Kernel panic"), "critical",
              "Kernel panic"),
    Signature("hung_task", "Hängender Prozess (hung task)",
              re.compile(r"blocked for more than \d+ seconds"), "high", "blocked for more than"),
    Signature("lockup", "CPU-Lockup", re.compile(r"soft lockup|hard LOCKUP"), "high", "lockup"),
    Signature("io_error", "I/O-Fehler am Datenträger",
              re.compile(r"Buffer I/O error on dev|blk_update_request: \S+ error|"
                         r"I/O error, dev \S+"), "high", "I/O error"),
    Signature("fs_error", "Dateisystemfehler",
              re.compile(r"EXT4-fs error|Remounting filesystem read-only|"
                         r"XFS \(\S+\): (Corruption|Internal error|metadata I/O error)|"
                         r"BTRFS (error|critical)"), "high", "EXT4-fs error"),
    Signature("mce", "Hardwarefehler (Machine Check)",
              re.compile(r"mce: \[Hardware Error\]|Machine check events logged"), "high",
              "Hardware Error"),
)
_BY_ID = {s.id: s for s in SIGNATURES}
_KERNEL = re.compile(r"(?:^|\s)kernel(?:\[\d+\])?:\s")


def critical_key(signature: str, host: str) -> str:
    """Wie LogTemplate.key (16 Zeichen) — Muster je Art UND Host, auch für Stummschalten."""
    return hashlib.md5(f"critical:{signature}:{host}".encode()).hexdigest()[:16]


def is_kernel_line(line) -> bool:
    if line.container:
        return False
    if (line.app or "").lower() in ("kernel", "kern"):
        return True
    return bool(_KERNEL.search(line.message[:200]))


def match(line) -> Signature | None:
    if not is_kernel_line(line):
        return None
    return next((s for s in SIGNATURES if s.pattern.search(line.message)), None)


@dataclass
class Found:
    signature: str
    host: str
    lines: int = 0
    examples: list[str] = field(default_factory=list)


def scan(lines) -> dict[tuple[str, str], Found]:
    """Kritische Kernelzeilen eines Fensters, je (Art, Host) zusammengefasst."""
    out: dict[tuple[str, str], Found] = {}
    for line in lines:
        sig = match(line)
        if sig is None or not line.source:
            continue
        host = line.source.lower()
        f = out.setdefault((sig.id, host), Found(sig.id, host))
        f.lines += 1
        first = line.message.split("\n", 1)[0][:400]
        if len(f.examples) < 5 and first not in f.examples:
            f.examples.append(first)
    return out


def should_escalate(recent_windows: int, th: Thresholds = DEFAULT) -> bool:
    return recent_windows >= th.critical_repeat_count


def reason_text(label: str, host: str, n: int, th: Thresholds = DEFAULT) -> str:
    return (f"{label} auf {host}: {n}× in den letzten {th.critical_repeat_hours} h "
            f"(gemeldet ab {th.critical_repeat_count}× — ein Ereignis je 5-min-Fenster)")


async def process(db, found: dict[tuple[str, str], Found], b: datetime, report: bool,
                  graylog_base: str = "", th: Thresholds = DEFAULT) -> list[dict]:
    """Ereignisse eines Fensters festhalten, Episoden führen, bei Wiederholung eskalieren.

    report=False (stilles Nachholen): festhalten ja, eskalieren nein — die nächste Meldung
    nach dem Nachholen zählt die nachgeholten Ereignisse mit.
    Rückgabe: zu meldende Kontexte (notify.on_critical).
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models.critical_event import CriticalEvent
    from app.models.error_cluster import ErrorCluster, LogTemplate
    from app.services.error_clusters.incident import close_cluster_incident, open_cluster_incident

    to_notify: list[dict] = []
    for (sig_id, host), f in found.items():
        sig = _BY_ID[sig_id]
        key = critical_key(sig_id, host)
        await db.execute(pg_insert(LogTemplate).values(
            key=key, template=f"{sig.label} auf {host}", examples=f.examples,
            first_seen=b, last_seen=b, muted=False,
        ).on_conflict_do_update(index_elements=["key"], set_={"last_seen": b, "examples": f.examples}))
        if (await db.execute(select(LogTemplate.muted).where(LogTemplate.key == key))).scalar():
            continue
        await db.execute(pg_insert(CriticalEvent).values(
            id=uuid.uuid4(), key=key, signature=sig_id, host=host, window_start=b,
            lines=f.lines, example=(f.examples or [""])[0], created_at=b,
        ).on_conflict_do_nothing(constraint="uq_critical_events_key_window"))
        since = b - timedelta(hours=th.critical_repeat_hours)
        recent = (await db.execute(select(func.count()).select_from(CriticalEvent).where(
            CriticalEvent.key == key, CriticalEvent.window_start > since,
            CriticalEvent.window_start <= b))).scalar() or 0

        ep = (await db.execute(select(ErrorCluster).where(
            ErrorCluster.source == "critical", ErrorCluster.key == key,
            ErrorCluster.status == "open"))).scalar_one_or_none()
        if ep is None:
            ep = ErrorCluster(id=uuid.uuid4(), key=key, source="critical", status="open",
                              rules=["critical"], notify_class="digest", started_at=b,
                              last_active_at=b, peak15=f.lines, total=0,
                              entities={host: 0}, sources=[host],
                              reasons=[reason_text(sig.label, host, recent, th)])
            db.add(ep)
            log.info("critical: %s on %s — first event (daily overview)", sig_id, host)
        ep.last_active_at = b
        # Aus den gespeicherten Ereignissen, nicht hochgezählt: ein nach Neustart erneut
        # verarbeitetes Fenster zählt so nicht doppelt.
        ep.total = (await db.execute(select(func.count()).select_from(CriticalEvent).where(
            CriticalEvent.key == key, CriticalEvent.window_start >= ep.started_at))).scalar() or 1
        ep.peak15 = max(ep.peak15 or 0, f.lines)
        ep.entities = {host: ep.total}
        if ep.incident_id is not None or not (report and should_escalate(recent, th)):
            continue

        first = (await db.execute(select(func.min(CriticalEvent.window_start)).where(
            CriticalEvent.key == key, CriticalEvent.window_start > since))).scalar() or b
        examples = [e for (e,) in (await db.execute(
            select(CriticalEvent.example).where(CriticalEvent.key == key,
                                                CriticalEvent.window_start > since)
            .order_by(CriticalEvent.window_start.desc()).limit(5))).all() if e]
        reason = reason_text(sig.label, host, recent, th)
        ep.notify_class = "immediate"
        ep.rules = ["critical", "repeated"]
        ep.reasons = [reason]
        ep.notified_at = b
        from urllib.parse import quote
        ctx = {
            "kind": "critical", "label": sig.label, "signature": sig_id, "template_key": key,
            "template": f"{sig.label} auf {host}", "examples": examples,
            "rules": ["critical", "repeated"], "reasons": [reason],
            "count15": f.lines, "expected15": None, "total": recent,
            "events": recent, "window_hours": th.critical_repeat_hours,
            "started_at": first.isoformat(), "until": b.isoformat(),
            "containers": [{"name": host, "count": recent}], "hosts": [host],
            "docker_hosts": [],
            "graylog_url": (f"{graylog_base.rstrip('/')}/search?q="
                            + quote(f'source:"{host}" AND message:"{sig.phrase}"')
                            + "&rangetype=relative&relative=21600") if graylog_base else None,
        }
        title = f"Kritisches Ereignis: {sig.label} auf {host} · {recent}× in {th.critical_repeat_hours} h"
        await db.flush()
        inc = await open_cluster_incident(db, kind="critical", title=title, severity=sig.severity,
                                          primary_host=host, context={"cluster_id": str(ep.id), **ctx},
                                          member_docs=[])
        ep.incident_id = inc.id
        to_notify.append({"ctx": ctx, "title": title, "cluster_id": str(ep.id),
                          "incident_id": str(inc.id)})
        log.info("critical: %s on %s — %d× in %d h → incident %s", sig_id, host, recent,
                 th.critical_repeat_hours, str(inc.id)[:8])

    # Ruhig geworden: schließen (Incident mit Begründung).
    quiet = b - timedelta(hours=th.critical_quiet_hours)
    for ep in (await db.execute(select(ErrorCluster).where(
            ErrorCluster.source == "critical", ErrorCluster.status == "open",
            ErrorCluster.last_active_at < quiet))).scalars():
        ep.status, ep.closed_at = "closed", b
        if ep.incident_id:
            await close_cluster_incident(db, str(ep.incident_id),
                                         f"seit {th.critical_quiet_hours} h kein weiteres Ereignis")
    return to_notify
