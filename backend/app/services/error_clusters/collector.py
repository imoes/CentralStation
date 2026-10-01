"""Ein 5-Minuten-Fenster aus Graylog holen und Zeilen je Muster zählen.

Geholt wird jede Fehlerzeile des Fensters (level:<=4 — Docker liefert jede Zeile
zusätzlich als Level-6-Kopie, ohne den Filter zählte alles doppelt). Switches
(nsa/nss/nsc) bleiben draußen wie im Feed-Collector; sie haben eigene Werkzeuge.

OpenSearch liefert höchstens 10 000 Treffer je Abfrage. Ist ein Fenster voller (bei
einer Flut: ~7 500 Flutzeilen + ~5 000 normale), wird es in Teilfenster zerlegt. Was
dann noch fehlt, ist als `complete=False` markiert — die Zählung ist eine Untergrenze,
und der Runner lässt Kandidaten-Muster von Graylog exakt nachzählen.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services.error_clusters.miner import Miner
from app.services.graylog_occurrences import iso

BASE_QUERY = "level:<=4 AND NOT source:(nsa* OR nss* OR nsc*)"
FIELDS = ["message", "source", "container_name", "timestamp", "level"]
MAX_RESULT_WINDOW = 10_000
SPLIT_AT = 9_500
MAX_PARTS = 8          # bis 80 000 Zeilen je 5 min vollständig; darüber Untergrenze


@dataclass
class Line:
    ts: str
    source: str
    container: str
    message: str

    @property
    def entity(self) -> str:
        """Wer die Zeile erzeugt hat: Container, sonst Host. Container sind keine Hosts."""
        return self.container or self.source


@dataclass
class Fetched:
    lines: list[Line]
    total: int            # laut Graylog im Fenster
    complete: bool        # alle Zeilen geholt?


def _line(m: dict) -> Line:
    return Line(
        ts=str(m.get("timestamp") or ""),
        source=str(m.get("source") or ""),
        container=str(m.get("container_name") or m.get("container_tag") or ""),
        message=str(m.get("message") or ""),
    )


async def fetch_window(svc, since: datetime, until: datetime,
                       query: str = BASE_QUERY) -> Fetched:
    """Alle Zeilen in [since, until), notfalls in Teilfenstern."""
    msgs, total = await svc.search_window(query, iso(since), iso(until),
                                          limit=MAX_RESULT_WINDOW, fields=FIELDS)
    if total <= SPLIT_AT or total <= len(msgs):
        return Fetched([_line(m) for m in msgs], total, True)
    parts = min(MAX_PARTS, math.ceil(total / (SPLIT_AT - 500)))
    step = (until - since) / parts
    lines: list[Line] = []
    complete = True
    for i in range(parts):
        a = since + step * i
        b = until if i == parts - 1 else since + step * (i + 1)
        part, part_total = await svc.search_window(query, iso(a), iso(b),
                                                   limit=MAX_RESULT_WINDOW, fields=FIELDS)
        lines.extend(_line(m) for m in part)
        complete = complete and part_total <= len(part)
    return Fetched(lines, total, complete)


# ── Zählen ───────────────────────────────────────────────────────────────────

@dataclass
class TemplateTally:
    template: str
    count: int = 0
    entities: Counter = field(default_factory=Counter)
    sources: set = field(default_factory=set)
    examples: list[str] = field(default_factory=list)    # bis 5 Rohzeilen (erste Zeile)

    def add(self, line: Line) -> None:
        self.count += 1
        self.entities[line.entity] += 1
        if line.source:
            self.sources.add(line.source)
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
