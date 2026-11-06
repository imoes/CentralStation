"""Ein 5-Minuten-Fenster aus Graylog holen und Zeilen je Muster zählen.

Geholt wird jede Fehlerzeile des Fensters:
- level:<=4 (Syslog, Journald, GELF mit Level), und
- Container-Zeilen mit level 6, die selbst einen Fehler melden. Docker liefert stdout
  pauschal als Level 6 (info); Java/Python-Anwendungen schreiben ihr eigenes "ERROR" in
  den Text. Ohne diese Zeilen blieb z. B. die DocumentIndex-Retry-Schleife der
  Content-Stores unsichtbar (12.10.: 6 663 solche Zeilen in 2 h, 0 davon mit level<=4).
  Graylog sucht ohne Groß-/Kleinschreibung, deshalb prüft ERROR_LINE die erste Zeile
  danach exakt (Großschreibung bzw. level=error) — "an error occurred" in einer
  Info-Meldung zählt nicht. Eine Level-6-Zeile, die im selben Fenster wortgleich auch mit
  level<=4 kam (Docker-Kopie, gemessen ~1 %), zählt nicht doppelt.
Switches (nsa/nss/nsc) bleiben draußen wie im Feed-Collector; sie haben eigene Werkzeuge.

OpenSearch liefert höchstens 10 000 Treffer je Abfrage. Ist ein Fenster voller (bei
einer Flut: ~7 500 Flutzeilen + ~5 000 normale), wird es in Teilfenster zerlegt. Was
dann noch fehlt, ist als `complete=False` markiert — die Zählung ist eine Untergrenze,
und der Runner lässt Kandidaten-Muster von Graylog exakt nachzählen.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services.error_clusters.miner import Miner
from app.services.graylog_occurrences import iso

BASE_QUERY = ("NOT source:(nsa* OR nss* OR nsc*) AND (level:<=4 OR (level:6 AND "
              "_exists_:container_name AND message:(ERROR OR FATAL OR CRITICAL OR SEVERE "
              "OR Traceback)) OR (NOT _exists_:level AND "
              "log_level:(emerg OR alert OR critical OR error)))")
#: Filebeat (Rolle img_filebeat, hyde_processor.js) liefert KEIN `level`, sondern ein aus
#: dem Text geschätztes `log_level`. Gemessen 13.10.: 45 % aller Graylog-Zeilen (2,36 von
#: 5,27 Mio./h, ≥ 73 Hosts) kamen so an und waren für die Erkennung unsichtbar — darunter
#: der OOM auf docker0209. Mitgelesen wird log_level emerg/alert/critical/error.
BEATS_ERROR_LEVELS = {"emerg", "alert", "critical", "error"}
#: Ändert sich, welche Zeilen gelesen werden, ändert sich diese Kennung — die Erkennung
#: weiß dann, dass Muster der neuen Quelle schon vorher gelaufen sein können (engine
#: COVERAGE_MARGIN: "anhaltend neu" anfangs nur als Tagesübersicht).
SOURCE_VERSION = "syslog-head-stripped-3"
_LEVEL_WORD = re.compile(r'\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|FATAL|CRITICAL|SEVERE)\b')
_ERROR_WORDS = {"ERROR", "FATAL", "CRITICAL", "SEVERE"}
_ERROR_FIELD = re.compile(
    r'\blevel=(error|fatal|critical)\b'
    r'|"level"\s*:\s*"(error|fatal|critical)"'
    r'|^Traceback \(most recent')


class _ErrorLine:
    """Meldet die Zeile selbst einen Fehler? Es zählt das ERSTE Level-Wort (Großschreibung):
    "INFO … outcome=ERROR" ist eine Info-Meldung der Anwendung, kein Fehler (gemessen 12.10.:
    Yarp3-Rückmeldungen). Ohne Level-Wort entscheiden level=error / JSON-level / Traceback."""

    @staticmethod
    def search(first_line: str) -> bool:
        m = _LEVEL_WORD.search(first_line)
        if m:
            return m.group(1) in _ERROR_WORDS
        return bool(_ERROR_FIELD.search(first_line))


ERROR_LINE = _ErrorLine()
FIELDS = ["message", "source", "container_name", "timestamp", "level", "log_level",
          "application_name", "facility"]
MAX_RESULT_WINDOW = 10_000
SPLIT_AT = 9_500
MAX_PARTS = 8          # bis 80 000 Zeilen je 5 min vollständig; darüber Untergrenze


@dataclass
class Line:
    ts: str
    source: str
    container: str
    message: str
    #: Programm laut Syslog (application_name/facility) — z. B. "kernel" (critical.py)
    app: str = ""

    @property
    def entity(self) -> str:
        """Wer die Zeile erzeugt hat: Container, sonst Host. Container sind keine Hosts."""
        return self.container or self.source


@dataclass
class Fetched:
    lines: list[Line]
    total: int            # laut Graylog im Fenster
    complete: bool        # alle Zeilen geholt?


def _level(m: dict) -> int:
    try:
        return int(m.get("level"))
    except (TypeError, ValueError):
        return 7


def keep_lines(msgs: list[dict]) -> list[dict]:
    """Level ≤4 immer; Level 6 nur mit Fehlerkennung im Text und ohne Level-≤4-Zwilling;
    ohne Level (Filebeat) nach log_level."""
    strong = {(m.get("container_name") or m.get("source"), m.get("message"))
              for m in msgs if _level(m) <= 4}
    out = []
    for m in msgs:
        if _level(m) <= 4:
            out.append(m)
            continue
        if m.get("level") is None:
            # Filebeat: kein Syslog-Level, das geschätzte log_level entscheidet (BASE_QUERY).
            if str(m.get("log_level") or "").lower() in BEATS_ERROR_LEVELS:
                out.append(m)
            continue
        first = str(m.get("message") or "").split("\n", 1)[0]
        if (ERROR_LINE.search(first)
                and (m.get("container_name") or m.get("source"), m.get("message")) not in strong):
            out.append(m)
    return out


#: Syslog-Kopf einer Filebeat-Zeile: [Zeitstempel] HOST PROGRAMM[PID]: …  — HOST wird nur
#: entfernt, wenn er der Quelle der Zeile entspricht (sonst ist es Text, kein Kopf).
_SYSLOG_HEAD = re.compile(
    r"^(?:\S+\s+){0,3}?(?P<host>[A-Za-z0-9][A-Za-z0-9_.-]*)\s+"
    r"(?=[A-Za-z0-9_./()-]+(?:\[\d+\])?:\s)")


def strip_syslog_head(message: str, source: str, raw_syslog: bool = False) -> str:
    """Zeitstempel und Host vor dem Programmnamen entfernen.

    Anlass 13.10.: ein Filebeat-Rollout änderte den Kopf von "<Zeit> <Host> dockerd[…]"
    zu "<Host> dockerd[…]" — jedes bekannte Rauschen wurde je Host zum NEUEN Muster
    (480 neue Muster in einem 5-min-Fenster, ~80 Fehl-Mails). Ohne Kopf ist das Muster
    unabhängig vom Format des Versenders, und derselbe Fehler auf 20 Hosts ist EIN Muster.
    """
    m = _SYSLOG_HEAD.match(message[:200])
    if not m:
        return message
    if raw_syslog:
        # Filebeat liest die Syslog-Datei: der Kopf ist sicher da, der Host darin heißt
        # aber oft anders als die Quelle (docker0209 schreibt "sammelpdf").
        return message[m.end():]
    if not source:
        return message
    host, src = m.group("host").lower(), source.lower()
    if src == host or src.startswith(host + ".") or host.startswith(src.split(".")[0]):
        return message[m.end():]
    return message


def _line(m: dict) -> Line:
    msg = str(m.get("message") or "")
    container = str(m.get("container_name") or m.get("container_tag") or "")
    source = str(m.get("source") or "")
    if not container:
        # Filebeat-Zeile = rohe Syslog-Zeile (log_level, aber kein Syslog-level-Feld)
        raw = m.get("level") is None and bool(m.get("log_level"))
        msg = strip_syslog_head(msg, source, raw_syslog=raw)
    return Line(
        ts=str(m.get("timestamp") or ""),
        source=source,
        container=container,
        message=msg,
        app=str(m.get("application_name") or m.get("facility") or ""),
    )


async def fetch_window(svc, since: datetime, until: datetime,
                       query: str = BASE_QUERY) -> Fetched:
    """Alle Zeilen in [since, until), notfalls in Teilfenstern."""
    msgs, total = await svc.search_window(query, iso(since), iso(until),
                                          limit=MAX_RESULT_WINDOW, fields=FIELDS)
    if total <= SPLIT_AT or total <= len(msgs):
        kept = keep_lines(msgs)
        return Fetched([_line(m) for m in kept], len(kept), True)
    parts = min(MAX_PARTS, math.ceil(total / (SPLIT_AT - 500)))
    step = (until - since) / parts
    lines: list[Line] = []
    complete = True
    seen, est = 0, 0.0
    for i in range(parts):
        a = since + step * i
        b = until if i == parts - 1 else since + step * (i + 1)
        part, part_total = await svc.search_window(query, iso(a), iso(b),
                                                   limit=MAX_RESULT_WINDOW, fields=FIELDS)
        kept = keep_lines(part)
        lines.extend(_line(m) for m in kept)
        complete = complete and part_total <= len(part)
        # Gesamtzahl hochrechnen: Graylog zählt auch, was der Nachfilter verwirft.
        seen += len(part)
        est += part_total * len(kept) / max(len(part), 1)
    return Fetched(lines, round(est) if not complete else len(lines), complete)


# ── Zählen ───────────────────────────────────────────────────────────────────

@dataclass
class TemplateTally:
    template: str
    count: int = 0
    entities: Counter = field(default_factory=Counter)
    sources: set = field(default_factory=set)
    examples: list[str] = field(default_factory=list)    # bis 5 Rohzeilen (erste Zeile)
    #: Zeilen je Host (Quelle) — für das Raster der Log-Überwachung (Muster × Host)
    hosts: Counter = field(default_factory=Counter)

    def add(self, line: Line) -> None:
        self.count += 1
        self.entities[line.entity] += 1
        if line.source:
            self.sources.add(line.source)
            self.hosts[line.source] += 1
        if len(self.examples) < 5:
            first = line.message.split("\n", 1)[0][:500]
            if first not in self.examples:
                self.examples.append(first)


@dataclass
class BucketTally:
    bucket_start: datetime
    templates: dict[str, TemplateTally]          # template_key → Zählung
    entities: Counter                             # Fehler je Container/Quelle insgesamt
    total: int
    complete: bool


def tally(lines: list[Line], miner: Miner, bucket_start: datetime,
          total: int | None = None, complete: bool = True) -> BucketTally:
    """Jede Zeile einem Muster zuordnen und zählen — keine Deduplizierung."""
    templates: dict[str, TemplateTally] = {}
    entities: Counter = Counter()
    for line in lines:
        mined = miner.add(line.message)
        if mined is None:
            continue
        t = templates.get(mined.key)
        if t is None:
            t = templates[mined.key] = TemplateTally(mined.template)
        else:
            t.template = mined.template      # Drain3 kann ein Muster verallgemeinern
        t.add(line)
        entities[line.entity] += 1
    return BucketTally(bucket_start, templates, entities,
                       total if total is not None else len(lines), complete)


def bucket_floor(ts: datetime, minutes: int = 5) -> datetime:
    ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return ts - timedelta(minutes=ts.minute % minutes)
