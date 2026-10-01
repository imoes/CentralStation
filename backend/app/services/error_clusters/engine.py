"""Von gezählten Fenstern zu Episoden und Meldungen — unabhängig vom Speicherort.

`process()` bekommt die Zählung eines abgeschlossenen 5-min-Fensters (collector.tally),
legt sie im Store ab, bewertet jedes betroffene Muster mit rules.evaluate und führt die
Episoden fort. Ergebnis sind Ereignisse ("open", "renotify", …), aus denen der Runner
Mails, Feed-Einträge und KI-Einschätzungen macht. Dieselbe Funktion treibt den Backtest
(MemoryStore) und den Betrieb (Postgres-Store, Block 3) — es gibt nur EINEN Regelweg.
"""
from __future__ import annotations

import itertools
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
    quantile_with_zeros,
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
class Episode:
    id: int
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

    def state(self) -> EpisodeState:
        return EpisodeState(self.started_at, self.last_active_at,
                            self.notified_rate, self.notified_entities)


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
        # key → {bucket_start: (count, Counter(entity → n))}
        self.buckets: dict[str, dict[datetime, tuple[int, Counter]]] = {}
        self.entity_buckets: dict[str, dict[datetime, int]] = {}
        self.open: dict[str, Episode] = {}
        self.closed: list[Episode] = []
        self.last_closed: dict[str, Episode] = {}
        self._ids = itertools.count(1)

    def next_episode_id(self) -> int:
        return next(self._ids)

    def prune(self, now: datetime) -> None:
        cut = now - HISTORY - BUCKET * WINDOW_BUCKETS
        for series in self.buckets.values():
            for b in [b for b in series if b < cut]:
                del series[b]
        for series in self.entity_buckets.values():
            for b in [b for b in series if b < cut]:
                del series[b]


def _record(store: MemoryStore, t: BucketTally) -> None:
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
        store.buckets.setdefault(key, {})[b] = (tt.count, ents)
    for ent, n in t.entities.items():
        store.entity_buckets.setdefault(ent, {})[b] = n


def _series_stats(series: dict[datetime, tuple[int, Counter]], window_start: datetime,
                  history_start: datetime, th: Thresholds):
    """Grundlast-Eingaben aus einer dünn besetzten Reihe: λ-Median, Perzentil, Container."""
    n = max(0, int((window_start - history_start) / BUCKET))
    nonzero_counts: list[int] = []
    nonzero_entities: list[int] = []
    sums: dict[int, int] = {}
    for b, (count, ents) in series.items():
        if not (history_start <= b < window_start) or count <= 0:
            continue
        nonzero_counts.append(count)
        nonzero_entities.append(len(ents))
        idx = int((b - history_start) / BUCKET)
        for w in range(idx - WINDOW_BUCKETS + 1, idx + 1):   # alle 15-min-Fenster mit b
            if 0 <= w <= n - WINDOW_BUCKETS:
                sums[w] = sums.get(w, 0) + count
    n_windows = max(0, n - WINDOW_BUCKETS + 1)
    q = quantile_with_zeros(list(sums.values()), n_windows, th.spike_quantile) if n_windows else None
    usual_entities = median_with_zeros(nonzero_entities, n)
    return n, nonzero_counts, q, usual_entities


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
        counts = tuple(series.get(window_start + BUCKET * i, (0, Counter()))[0]
                       for i in range(WINDOW_BUCKETS))
        ents15: Counter = Counter()
        srcs15: set = set()
        for i in range(WINDOW_BUCKETS):
            ents15.update(series.get(window_start + BUCKET * i, (0, Counter()))[1])
        if key in t.templates:
            srcs15 |= t.templates[key].sources
        n, nonzero, q, usual = _series_stats(series, window_start, history_start, th)
        # Lernphase: noch keine Grundlast, nichts melden (rules.baseline, dünn besetzt).
        lam = (None if detector_age < timedelta(hours=th.warmup_hours)
               else max(median_with_zeros(nonzero, n), th.lam_floor))
        window = Window(counts=counts, entities=len(ents15), sources=len(srcs15) or 1,
                        usual_entities=usual, seen_before=bool(nonzero))
        verdict = evaluate(window, lam, th=th, history_q=q)

        info = store.templates[key]
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
        episode.peak15 = max(episode.peak15, verdict.count15)
        episode.total += counts[-1]
        episode.entities.update(series.get(b, (0, Counter()))[1])
        episode.sources |= srcs15

        mail = action in ("open", "renotify") or (
            action == "reopen" and verdict.notify == "immediate"
            and should_renotify(counts[-1], len(ents15), episode.state(), th))
        if mail:
            episode.notify_class = "immediate"
            episode.notified_rate = max(counts)
            episode.notified_entities = len(ents15)
            episode.notified_at = now

        if action == "close":
            episode.closed_at = now
            store.closed.append(store.open.pop(key))
            store.last_closed[key] = episode

        if action != "update":
            events.append(Event(action, episode, info, verdict, b, len(ents15), mail))

    store.prune(now)
    return events
