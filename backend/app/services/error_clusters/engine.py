"""Von gezählten Fenstern zu Episoden und Meldungen — unabhängig vom Speicherort.

`process()` bekommt die Zählung eines abgeschlossenen 5-min-Fensters (collector.tally),
legt sie im Store ab, bewertet jedes betroffene Muster mit rules.evaluate und führt die
Episoden fort. Ergebnis sind Ereignisse ("open", "renotify", …), aus denen der Runner
Mails, Feed-Einträge und KI-Einschätzungen macht. Dieselbe Funktion treibt den Backtest
(MemoryStore) und den Betrieb (Postgres-Store, Block 3) — es gibt nur EINEN Regelweg.
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.error_clusters.collector import BucketTally
from app.services.error_clusters.rules import (
    DEFAULT,
    WINDOW_BUCKETS,
    EpisodeState,
    Thresholds,
    Verdict,
    Window,
    evaluate,
    median_with_zeros,
    next_action,
    should_renotify,
)

BUCKET = timedelta(minutes=5)
HISTORY = timedelta(days=14)
MAX_BUCKET_ENTITIES = 50


@dataclass
class TemplateInfo:
    key: str
    template: str
    first_seen: datetime
    last_seen: datetime
    muted: bool = False
    examples: list[str] = field(default_factory=list)


@dataclass
class BucketRec:
    count: int
    n_entities: int
    #: Container-Aufteilung — nur für die letzten drei Fenster gehalten (Ausbreitung,
    #: Mail); ältere Fenster brauchen nur die Anzahl, für die übliche Breite.
    entities: Counter | None = None


@dataclass
class Episode:
    id: str
    key: str
    started_at: datetime
    last_active_at: datetime
    rules: set[str] = field(default_factory=set)
    notify_class: str = "none"            # "immediate" | "digest"
    notified_rate: int = 0                 # höchste 5-min-Rate bei der letzten Mail
    notified_entities: int = 0
    notified_at: datetime | None = None
    peak15: int = 0
    total: int = 0
    entities: Counter = field(default_factory=Counter)
    sources: set = field(default_factory=set)
    closed_at: datetime | None = None
    #: Gehört zu einem anderen, schon gemeldeten Ausbruch (dessen Episoden-ID): selbe
    #: Zeit, gemeinsame Container/Hosts. Dann EIN Incident, EINE Mail — dieses Muster
    #: erscheint dort als weiteres.
    group_id: str | None = None
    #: Lesbare Begründung der letzten meldepflichtigen Bewertung (für API, Mail, Widget).
    reasons: list = field(default_factory=list)

    def state(self) -> EpisodeState:
        return EpisodeState(self.started_at, self.last_active_at,
                            self.notified_rate, self.notified_entities, self.notified_at)


@dataclass
class Event:
    action: str                           # open | open_digest | reopen | renotify | close
    episode: Episode
    template: TemplateInfo
    verdict: Verdict
    bucket_start: datetime
    entities15: int
    mail: bool                            # löst dieses Ereignis eine Sofort-Mail aus?


class MemoryStore:
    """Alles im Speicher — für Backtest und Tests. Gleiche Schnittstelle wie der DB-Store."""

    def __init__(self, detector_started_at: datetime) -> None:
        self.detector_started_at = detector_started_at
        self.templates: dict[str, TemplateInfo] = {}
        self.buckets: dict[str, dict[datetime, BucketRec]] = {}
        self.entity_buckets: dict[str, dict[datetime, int]] = {}
        self.open: dict[str, Episode] = {}
        self.closed: list[Episode] = []
        self.last_closed: dict[str, Episode] = {}
        #: Was dieser Lauf geändert hat — der DB-Store schreibt genau das (runner.persist).
        self.dirty_templates: set[str] = set()
        self.new_buckets: list[tuple[str, datetime]] = []
        self.dirty_episodes: dict[str, Episode] = {}

    def next_episode_id(self) -> str:
        return str(uuid.uuid4())

    def prune(self, now: datetime) -> None:
        cut = now - HISTORY - BUCKET * WINDOW_BUCKETS
        keep_entities = now - BUCKET * WINDOW_BUCKETS
        for series in self.buckets.values():
            for b in [b for b in series if b < cut]:
                del series[b]
            for b, rec in series.items():
                if rec.entities is not None and b < keep_entities:
                    rec.entities = None
        for series in self.entity_buckets.values():
            for b in [b for b in series if b < cut]:
                del series[b]


def _record(store: MemoryStore, t: BucketTally) -> None:
    store.dirty_templates.clear()
    store.new_buckets.clear()
    store.dirty_episodes.clear()
    b = t.bucket_start
    for key, tt in t.templates.items():
        info = store.templates.get(key)
        if info is None:
            store.templates[key] = TemplateInfo(key, tt.template, b, b, examples=list(tt.examples))
        else:
            info.template = tt.template
            info.last_seen = b
            if len(info.examples) < 5:
                info.examples.extend(e for e in tt.examples if e not in info.examples)
                del info.examples[5:]
        ents = Counter(dict(tt.entities.most_common(MAX_BUCKET_ENTITIES)))
        store.buckets.setdefault(key, {})[b] = BucketRec(tt.count, len(tt.entities), ents)
        store.dirty_templates.add(key)
        store.new_buckets.append((key, b))
    for ent, n in t.entities.items():
        store.entity_buckets.setdefault(ent, {})[b] = n


def _series_stats(series: dict[datetime, BucketRec], window_start: datetime,
                  history_start: datetime, th: Thresholds):
    """Grundlast-Eingaben aus einer dünn besetzten Reihe: λ-Median, Perzentil, Container."""
    n = max(0, int((window_start - history_start) / BUCKET))
    nonzero_counts: list[int] = []
    nonzero_entities: list[int] = []
    days: set = set()
    sums: dict[int, int] = {}
    for b, rec in series.items():
        count = rec.count
        if not (history_start <= b < window_start) or count <= 0:
            continue
        nonzero_counts.append(count)
        nonzero_entities.append(rec.n_entities)
        days.add(b.date())
        idx = int((b - history_start) / BUCKET)
        for w in range(idx - WINDOW_BUCKETS + 1, idx + 1):   # alle 15-min-Fenster mit b
            if 0 <= w <= n - WINDOW_BUCKETS:
                sums[w] = sums.get(w, 0) + count
    peak = float(max(sums.values())) if sums else 0.0
    usual_entities = median_with_zeros(nonzero_entities, n)
    return n, nonzero_counts, peak, usual_entities, len(days)


def _group_leader(store: MemoryStore, episode: Episode, b: datetime,
                  th: Thresholds) -> Episode | None:
    """Ein gemeldeter Ausbruch, zu dem dieses Muster gehört — oder None.

    Kriterien: der andere wurde gemeldet, begann höchstens group_minutes vorher und teilt
    Container (Jaccard ≥ group_overlap) oder Hosts. Beispiel 30.09.: der 401 auf 24
    Zipline-Containern, 25 min später der HealthCheck-Fehler auf denselben Containern —
    ein Vorfall, nicht zwei Mails.
    """
    mine = set(episode.entities)
    best, best_score = None, 0.0
    for other in store.open.values():
        if other.key == episode.key or other.notified_at is None and other.group_id is None:
            continue
        if other.notify_class != "immediate":
            continue
        if b - other.started_at > timedelta(minutes=th.group_minutes) or other.started_at > b:
            continue
        theirs = set(other.entities)
        union = mine | theirs
        jaccard = len(mine & theirs) / len(union) if union else 0.0
        shared_hosts = bool(episode.sources & other.sources)
        score = jaccard + (0.5 if shared_hosts else 0.0)
        if (jaccard >= th.group_overlap or shared_hosts) and score > best_score:
            best, best_score = other, score
    return best


def process(store: MemoryStore, t: BucketTally, th: Thresholds = DEFAULT) -> list[Event]:
    """Ein abgeschlossenes Fenster einarbeiten und die Ereignisse dieses Laufs liefern."""
    _record(store, t)
    b = t.bucket_start
    now = b + BUCKET                       # Bewertung zum Ende des Fensters
    window_start = b - BUCKET * (WINDOW_BUCKETS - 1)
    history_start = max(store.detector_started_at, window_start - HISTORY)
    detector_age = now - store.detector_started_at
    events: list[Event] = []

    for key in set(t.templates) | set(store.open):
        series = store.buckets.get(key, {})
        recs = [series.get(window_start + BUCKET * i) for i in range(WINDOW_BUCKETS)]
        counts = tuple(r.count if r else 0 for r in recs)
        ents15: Counter = Counter()
        srcs15: set = set()
        for r in recs:
            if r and r.entities:
                ents15.update(r.entities)
        if key in t.templates:
            srcs15 |= t.templates[key].sources
        n, nonzero, peak, usual, active_days = _series_stats(series, window_start, history_start, th)
        # Lernphase: noch keine Grundlast, nichts melden (rules.baseline, dünn besetzt).
        lam = (None if detector_age < timedelta(hours=th.warmup_hours)
               else max(median_with_zeros(nonzero, n), th.lam_floor))
        window = Window(counts=counts, entities=len(ents15), sources=len(srcs15) or 1,
                        usual_entities=usual, seen_before=bool(nonzero), active_days=active_days)
        verdict = evaluate(window, lam, th=th, history_peak=peak)

        info = store.templates.get(key)
        if info is None:
            # Episode ohne bekanntes Muster (z. B. falsch geladen): überspringen statt den
            # ganzen Lauf abzubrechen — sonst stünde die Erkennung für ALLE Muster.
            continue
        episode = store.open.get(key)
        action = next_action(verdict=verdict, episode=episode.state() if episode else None,
                             muted=info.muted,
                             last_closed_at=(store.last_closed[key].closed_at
                                             if key in store.last_closed else None),
                             now=now, lam=lam, last_count=counts[-1],
                             entities=len(ents15), th=th)
        if action == "none":
            continue

        if action in ("open", "open_digest"):
            episode = Episode(id=store.next_episode_id(), key=key, started_at=b,
                              last_active_at=b,
                              notify_class="immediate" if verdict.notify == "immediate" else "digest")
            store.open[key] = episode
        elif action == "reopen":
            # Innerhalb des Cooldowns geht die ALTE Episode weiter — sonst wäre der
            # Cooldown wirkungslos und jedes Aufflackern eine neue Mail.
            episode = store.last_closed.pop(key)
            episode.closed_at = None
            store.closed.remove(episode)
            store.open[key] = episode
        assert episode is not None

        if lam is None or counts[-1] >= max(th.episode_active_factor * lam, 1):
            episode.last_active_at = b
        episode.rules |= set(verdict.rules)
        if verdict.reasons and verdict.rules:
            episode.reasons = list(verdict.reasons)
        episode.peak15 = max(episode.peak15, verdict.count15)
        episode.total += counts[-1]
        cur = series.get(b)
        if cur and cur.entities:
            episode.entities.update(cur.entities)
        episode.sources |= srcs15

        mail = action in ("open", "renotify") or (
            action == "reopen" and verdict.notify == "immediate"
            and should_renotify(counts[-1], len(ents15), episode.state(), th))
        if mail and action in ("open", "reopen") and episode.group_id is None:
            leader = _group_leader(store, episode, b, th)
            if leader is not None:
                episode.group_id = leader.group_id or leader.id
        if episode.group_id is not None and mail:
            # Teil eines schon gemeldeten Ausbruchs: keine eigene Mail. notify_class
            # trotzdem "immediate" — für Feed, Incident und Widget ist es ein Ausbruch.
            episode.notify_class = "immediate"
            mail = False
        if mail:
            episode.notify_class = "immediate"
            episode.notified_rate = max(counts)
            episode.notified_entities = len(ents15)
            episode.notified_at = now

        if action == "close":
            episode.closed_at = now
            store.closed.append(store.open.pop(key))
            store.last_closed[key] = episode

        store.dirty_episodes[episode.id] = episode
        if action != "update":
            events.append(Event(action, episode, info, verdict, b, len(ents15), mail))

    store.prune(now)
    return events
