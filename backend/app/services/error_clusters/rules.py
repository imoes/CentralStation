"""Wann ein Fehlermuster ein Vorfall ist — reine Funktionen, ohne Graylog, DB oder LLM.

Grundlage sind Zählwerte je Muster in 5-Minuten-Fenstern; bewertet werden immer die
letzten drei Fenster (15 min). Die Regeln folgen dem, was Datadog Watchdog, Elastic
(count/rare by category), Sumo LogCompare und Grafana Sift unterscheiden:

* NEU         — ein Muster, das es 14 Tage nicht gab.
* SPITZE      — ein bekanntes Muster weit über seiner Grundlast.
* AUSBREITUNG — ein Muster auf viel mehr Containern/Quellen als üblich. Das stärkste
                einzelne Signal: der 401-Vorfall traf 26 Container zugleich.

Grundlast ist der MEDIAN, kein gleitender Mittelwert. Der Mittelwert hätte Welle 1 des
401-Vorfalls (103 835 Zeilen) noch ~20 h später mitgeschleppt und Welle 2 verfehlt; der
Median eines Musters, das 99 % der Zeit schweigt, bleibt bei ~0.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

BUCKET_MINUTES = 5
WINDOW_BUCKETS = 3          # bewertet werden 15 min
BUCKETS_PER_HOUR = 60 // BUCKET_MINUTES


@dataclass(frozen=True)
class Thresholds:
    warmup_hours: float = 24.0        # davor: lernen, nichts melden
    seasonal_days: float = 14.0       # ab dann: Grundlast je Wochenstunde
    lam_floor: float = 0.2            # Grundlast nie 0 — sonst wäre jede Zeile "unendlich viel"
    new_min_count: int = 20           # NEU: mindestens so viele Zeilen in 15 min …
    new_min_sources: int = 2          # … oder auf so vielen Quellen
    new_immediate_count: int = 500    # NEU ohne Ausbreitung, aber so laut → sofort
    spike_min_count: int = 50
    spike_factor: float = 5.0         # × erwartete Menge in 15 min
    spike_quantile: float = 0.995     # oder über diesem Perzentil der 15-min-Summen
    spike_p: float = 1e-6             # Poisson-Wahrscheinlichkeit, dass es Zufall ist
    spike_bucket_factor: float = 3.0  # ein einzelnes Fenster gilt ab 3 λ als erhöht …
    spike_buckets_needed: int = 2     # … und so viele der drei Fenster müssen es sein
    spread_min_entities: int = 5
    spread_factor: float = 3.0
    episode_active_factor: float = 2.0
    episode_quiet_minutes: int = 60
    cooldown_hours: float = 3.0
    renotify_factor: float = 2.0
    renotify_min_new_entities: int = 5
    fleet_min_hosts: int = 5
    fleet_factor: float = 3.0


DEFAULT = Thresholds()


# ── Statistik ─────────────────────────────────────────────────────────────────

def poisson_sf(k: int, mu: float) -> float:
    """P(X ≥ k) für X ~ Poisson(mu), numerisch stabil im Log-Raum.

    Nur der rechte Rand wird gebraucht (k > mu). Für k ≤ mu ist das Ergebnis für die
    Frage "ist das ungewöhnlich viel?" ohnehin nicht signifikant — dann 1.0.
    """
    if k <= 0:
        return 1.0
    if mu <= 0:
        return 0.0
    if k <= mu:
        return 1.0
    log_term = -mu + k * math.log(mu) - math.lgamma(k + 1)
    log_sum = log_term
    i = k
    while True:
        i += 1
        log_term += math.log(mu) - math.log(i)
        if log_term < log_sum - 30:          # Beitrag < e^-30 der Summe
            break
        log_sum = max(log_sum, log_term) + math.log1p(math.exp(-abs(log_sum - log_term)))
    return math.exp(log_sum)


def quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def sums15(history: Sequence[int]) -> list[int]:
    """Gleitende Summen über je drei Fenster — die Einheit, in der bewertet wird."""
    return [sum(history[i:i + WINDOW_BUCKETS])
            for i in range(0, max(0, len(history) - WINDOW_BUCKETS + 1))]


def baseline(
    history: Sequence[int],
    detector_age: timedelta,
    same_hour_of_week: Sequence[int] | None = None,
    th: Thresholds = DEFAULT,
) -> float | None:
    """Grundlast λ je 5-Minuten-Fenster, oder None während der Lernphase.

    history: 5-min-Zählwerte VOR dem bewerteten Fenster, älteste zuerst, Nullen
    eingeschlossen (seit Erkennungsstart, höchstens 14 Tage).
    same_hour_of_week: Stundensummen derselben Wochenstunde der Vorwochen — ab 14 Tagen
    Laufzeit, damit der tägliche Rhythmus nicht als Spitze gilt.
    """
    if detector_age < timedelta(hours=th.warmup_hours):
        return None
    if (detector_age >= timedelta(days=th.seasonal_days)
            and same_hour_of_week and len(same_hour_of_week) >= 2):
        lam = statistics.median(same_hour_of_week) / BUCKETS_PER_HOUR
    elif history:
        lam = statistics.median(history)
    else:
        lam = 0.0
    return max(float(lam), th.lam_floor)


# ── Bewertung eines Fensters ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Window:
    """Ein Muster in den letzten 15 Minuten."""
    counts: tuple[int, ...]           # die letzten drei 5-min-Zählwerte, älteste zuerst
    entities: int                     # verschiedene Container/Quellen in diesen 15 min
    sources: int                      # verschiedene Hosts (Graylog-source)
    usual_entities: float             # übliche Anzahl Container je 15 min (Median)
    seen_before: bool                 # in den 14 Tagen vor diesem Fenster aufgetreten


@dataclass(frozen=True)
class Verdict:
    rules: frozenset[str]             # {"new", "spike", "spread"}
    notify: str                       # "immediate" | "digest" | "none"
    count15: int
    expected15: float | None
    p_value: float | None
    reasons: tuple[str, ...] = field(default=())   # lesbare Begründung, für Mail und UI


def evaluate(window: Window, lam: float | None, history: Sequence[int],
             th: Thresholds = DEFAULT) -> Verdict:
    """Welche Regeln greifen und welche Art Meldung daraus folgt."""
    count15 = sum(window.counts)
    if lam is None:
        return Verdict(frozenset(), "none", count15, None, None, ("Lernphase",))

    expected = WINDOW_BUCKETS * lam
    p = poisson_sf(count15, expected)
    rules: set[str] = set()
    reasons: list[str] = []

    if not window.seen_before and (count15 >= th.new_min_count
                                   or window.sources >= th.new_min_sources):
        rules.add("new")
        reasons.append("neues Muster (14 Tage nicht gesehen)")

    elevated = sum(1 for c in window.counts if c >= th.spike_bucket_factor * lam)
    sums = sums15(history)
    over_quantile = bool(sums) and count15 > quantile(sums, th.spike_quantile)
    if (count15 >= th.spike_min_count
            and (count15 >= th.spike_factor * expected or over_quantile)
            and p < th.spike_p
            and elevated >= th.spike_buckets_needed):
        rules.add("spike")
        reasons.append(f"{count15} Zeilen in 15 min, erwartet {expected:.1f} "
                       f"({count15 / expected:.0f}×)")

    if (window.entities >= th.spread_min_entities
            and window.entities >= th.spread_factor * max(window.usual_entities, 1.0)):
        rules.add("spread")
        reasons.append(f"{window.entities} Container/Quellen, üblich "
                       f"{max(window.usual_entities, 1.0):.0f}")

    if "spike" in rules or "spread" in rules:
        notify = "immediate"
    elif "new" in rules:
        notify = "immediate" if count15 >= th.new_immediate_count else "digest"
    else:
        notify = "none"
    return Verdict(frozenset(rules), notify, count15, expected, p, tuple(reasons))


def fleet_cluster(hosts_now: int, usual_hosts: float, th: Thresholds = DEFAULT) -> bool:
    """CheckMK: derselbe Service auf vielen Hosts zugleich WARN/CRIT."""
    return hosts_now >= th.fleet_min_hosts and hosts_now >= th.fleet_factor * max(usual_hosts, 1.0)


# ── Episoden ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EpisodeState:
    started_at: datetime
    last_active_at: datetime
    notified_count15: int             # Menge in 15 min bei der letzten Mail (0 = keine)
    notified_entities: int


def next_action(
    *,
    verdict: Verdict,
    episode: EpisodeState | None,
    muted: bool,
    last_closed_at: datetime | None,
    now: datetime,
    lam: float | None,
    last_count: int,
    entities: int,
    th: Thresholds = DEFAULT,
) -> str:
    """Was mit einem Muster in diesem Lauf geschieht.

    Ergebnis: "open" | "open_digest" | "reopen" | "renotify" | "update" | "close" | "none".
    Eine Mail folgt aus "open" (sofort), "open_digest" (Tagesübersicht), "renotify" und
    bei "reopen" nur, wenn sie zugleich die Erneut-Melden-Schwelle erreicht (Aufrufer).
    """
    if muted:
        return "none"
    if episode is not None:
        active = lam is not None and last_count > 0 and last_count >= th.episode_active_factor * lam
        if active:
            if verdict.notify == "immediate" and should_renotify(
                    verdict.count15, entities, episode, th):
                return "renotify"
            return "update"
        if now - episode.last_active_at >= timedelta(minutes=th.episode_quiet_minutes):
            return "close"
        return "update"
    if verdict.notify == "none":
        return "none"
    if last_closed_at is not None and now - last_closed_at < timedelta(hours=th.cooldown_hours):
        return "reopen"
    return "open" if verdict.notify == "immediate" else "open_digest"


def should_renotify(count15: int, entities: int, episode: EpisodeState,
                    th: Thresholds = DEFAULT) -> bool:
    """Erneut melden nur, wenn sich die Lage verdoppelt hat — sonst wäre jeder Lauf eine Mail."""
    if episode.notified_count15 <= 0:
        return True     # bisher nur in der Tagesübersicht → jetzt ist es ein Ausbruch
    if count15 >= th.renotify_factor * episode.notified_count15:
        return True
    return (entities >= th.renotify_factor * max(episode.notified_entities, 1)
            and entities - episode.notified_entities >= th.renotify_min_new_entities)
