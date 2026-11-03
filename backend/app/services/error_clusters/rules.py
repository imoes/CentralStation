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
PERSIST_PARTS = 4           # "anhaltend": in jeder halben Stunde der letzten 2 h


@dataclass(frozen=True)
class Thresholds:
    warmup_hours: float = 24.0        # davor: lernen, nichts melden
    seasonal_days: float = 14.0       # ab dann: Grundlast je Wochenstunde
    lam_floor: float = 0.2            # Grundlast nie 0 — sonst wäre jede Zeile "unendlich viel"
    new_min_count: int = 20           # NEU: mindestens so viele Zeilen in 15 min …
    new_min_sources: int = 2          # … oder auf so vielen Quellen
    new_immediate_count: int = 500    # NEU ohne Ausbreitung, aber so laut → sofort
    # Schwellen aus dem Backtest (72 h): mit 50 Zeilen und "Faktor ODER Perzentil" kamen
    # 183 Sofort-Mails in 48 h — kleine Spitzen eines Containers, täglich wiederkehrende
    # Muster (systemd/GnuPG), wenige Zeilen auf 5 Quellen.
    spike_min_count: int = 200
    spike_factor: float = 5.0         # × erwartete Menge in 15 min
    spike_immediate_count: int = 1000 # Spitze ohne Ausbreitung: erst ab hier sofort
    spike_p: float = 1e-6             # Poisson-Wahrscheinlichkeit, dass es Zufall ist
    spike_bucket_factor: float = 3.0  # ein einzelnes Fenster gilt ab 3 λ als erhöht …
    spike_buckets_needed: int = 2     # … und so viele der drei Fenster müssen es sein
    spread_min_entities: int = 8
    spread_min_count: int = 100
    spread_factor: float = 3.0
    episode_active_factor: float = 2.0
    episode_quiet_minutes: int = 60
    cooldown_hours: float = 3.0
    renotify_factor: float = 2.0
    renotify_min_new_entities: int = 5
    renotify_min_minutes: int = 60    # höchstens eine erneute Mail je Stunde und Episode
    recurring_days: int = 3           # ab so vielen aktiven Tagen gilt ein Muster als wiederkehrend …
    #: … und muss dann seinen bisherigen 15-min-Höchstwert um diesen Faktor übertreffen.
    #: Nicht das 99,5-Perzentil: ein täglicher Job erreicht seinen Höchstwert in ~0,35 %
    #: der Fenster — das Perzentil läge darunter und jeder Lauf wäre wieder eine Spitze.
    recurring_peak_factor: float = 2.0
    group_minutes: int = 30           # Muster, die so nah beieinander beginnen …
    group_overlap: float = 0.2        # … und Container teilen, sind EIN Vorfall
    fleet_min_hosts: int = 5
    fleet_factor: float = 3.0
    #: WIEDERKEHREND (Flotte): derselbe Service fiel an ≥ fleet_recurring_days der letzten
    #: fleet_recurring_lookback_days Tage zur gleichen Uhrzeit (± Toleranz) auf
    #: ≥ fleet_min_hosts Hosts aus → geplanter Job, kein Vorfall: Tagesübersicht statt
    #: Incident/Mail. Anlass 14.10.: Orderserver ORD1/2 jede Nacht 00:30–05:00 Ortszeit
    #: (11 Incidents in 14 Tagen), ebenso Proxmox-Backup 23:03 (12), Storage Pool 21:33 (7).
    #: Der 7-Tage-Median aller 15-min-Fenster ist für einen täglichen Lauf 0 — die
    #: Flotten-Regel allein hält jede Nacht für ungewöhnlich.
    fleet_recurring_days: int = 3
    fleet_recurring_lookback_days: int = 7
    fleet_recurring_tolerance_minutes: int = 30
    #: Ein früherer Tag zählt nur, wenn der Ausfall dort ähnlich groß war (≥ share × heute)
    #: UND der Service außerhalb des Zeitfensters höchstens max_outside 15-min-Fenster mit
    #: ≥ fleet_min_hosts hatte — ein Job ist an seine Uhrzeit gebunden. Backtest 14 Tage:
    #: ohne diese Bedingung galten 12 von 15 Check_MK-Ausfällen als "wiederkehrend" (der
    #: Dienst war 30.09.–02.10. ganztägig gestört), mit ihr 0; die Jobs bleiben erkannt.
    #: KRITISCHES EREIGNIS (critical.py): dieselbe schwere Kernelmeldung (OOM-Kill, Kernel
    #: panic, I/O-Fehler …) auf demselben Host in ≥ critical_repeat_count 5-min-Fenstern
    #: binnen critical_repeat_hours → Incident + Mail. Einzelnes Auftreten: Tagesübersicht.
    #: Anlass 14.10.: docker0209 hatte 25+ h lang stündlich einen OOM — 1–3 Kernelzeilen,
    #: für die mengenbasierten Regeln unsichtbar. Gemessen 7 Tage Flotte: 14 Host/Art-
    #: Kombinationen, davon erreicht nur vpp0177 (MCE, 246 Fenster) die Schwelle.
    #: QUELLENWECHSEL: so viele neue Muster in einem 5-min-Fenster → Beginn der Beobachtung
    #: (engine.fresh_at_coverage). Normalbetrieb 05.–12.10.: Median 1, p99 18, max 46;
    #: Filebeat-Rollout 13.10. 13:55: 480, danach 101/92/87/74 (≈ 80 Fehl-Mails).
    burst_new_templates: int = 60
    critical_repeat_count: int = 3
    critical_repeat_hours: int = 6
    critical_quiet_hours: int = 6
    fleet_recurring_share: float = 0.5
    fleet_recurring_max_outside: int = 2
    #: ANHALTEND NEU: ein Muster, das erst seit höchstens persist_max_age_hours existiert und
    #: in JEDER halben Stunde der letzten 2 h auftrat (persist_window_buckets 5-min-Fenster in
    #: PERSIST_PARTS Teilen), zusammen ≥ persist_min_total Zeilen. Anlass 12.10.: eine
    #: Retry-Schleife nach einem Deploy (DocumentIndex, schubweise alle ~10 min, ein Container)
    #: blieb unter NEU (20 in 15 min) — im ersten Fenster zu leise, danach galt das Muster als
    #: "gesehen" und wurde nie wieder als neu bewertet. Halbstunden statt 5-min-Fenster, weil
    #: Retry-Schleifen schubweise laufen (gemessen: 1 464 Zeilen in 2 h, aber nur 12/24 Fenster).
    persist_max_age_hours: float = 24.0
    persist_window_buckets: int = 24
    persist_min_total: int = 100
    #: Erst ab diesem Alter wird gemeldet (Mail, Flotten-Incident). Ein Neustart oder ein
    #: nächtlicher Job macht Dienste für Minuten rot und ist dann vorbei — 10/2026 waren
    #: das die meisten Sofort-Mails (aktiv 0–15 min). Ein echtes Problem besteht weiter.
    min_age_minutes: int = 30


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


def median_with_zeros(nonzero: Sequence[float], n: int) -> float:
    """Median einer Reihe aus `n` Werten, von denen nur `nonzero` nicht 0 sind.

    Fast alle Muster schweigen die meiste Zeit — ihre Historie ist überwiegend 0. Statt
    4 032 Werte (14 Tage × 288) je Muster und Lauf zu sortieren, genügen die wenigen
    Treffer: dasselbe Ergebnis wie statistics.median über die volle Reihe.
    """
    if n <= 0:
        return 0.0
    k = len(nonzero)
    if k * 2 < n:
        return 0.0
    vals = sorted(nonzero)
    zeros = n - k

    def at(i: int) -> float:
        return 0.0 if i < zeros else vals[i - zeros]

    if n % 2:
        return at(n // 2)
    return (at(n // 2 - 1) + at(n // 2)) / 2


def quantile_with_zeros(nonzero: Sequence[float], n: int, q: float) -> float:
    """Wie quantile(), für eine Reihe aus n Werten mit nur `nonzero` ungleich 0."""
    if n <= 0:
        return 0.0
    vals = sorted(nonzero)
    zeros = n - len(vals)
    pos = q * (n - 1)
    lo, hi = math.floor(pos), math.ceil(pos)

    def at(i: int) -> float:
        return 0.0 if i < zeros else vals[i - zeros]

    return at(lo) + (at(hi) - at(lo)) * (pos - lo)


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
    #: An wie vielen verschiedenen Tagen das Muster in der Historie aktiv war. Erst ein
    #: WIEDERKEHRENDES Muster (täglicher Job) muss auch sein bisheriges Höchstniveau
    #: übertreffen; ein wiederholter Ausbruch (ein, zwei Tage) nicht.
    active_days: int = 0
    #: Stunden seit dem ersten Auftreten (None = älter als die Historie / unbekannt)
    first_seen_hours: float | None = None
    #: Letzte persist_window_buckets 5-min-Fenster: in wie vielen der PERSIST_PARTS Teile
    #: aktiv, wie viele Zeilen insgesamt
    recent_parts: int = 0
    recent_total: int = 0
    #: Muster erschien kurz nach Beginn der Beobachtung einer Quelle, und die Grundlast dafür
    #: fehlt noch (< 24 h): SPITZE, AUSBREITUNG und NEU sind nicht entscheidbar.
    coverage_unsure: bool = False
    #: Muster erschien kurz nach Beginn der Beobachtung einer Quelle (ohne Ablauf): es lief
    #: womöglich schon vorher — ANHALTEND NEU dann nur in die Tagesübersicht, nie als Mail.
    present_at_start: bool = False


@dataclass(frozen=True)
class Verdict:
    rules: frozenset[str]             # {"new", "spike", "spread"}
    notify: str                       # "immediate" | "digest" | "none"
    count15: int
    expected15: float | None
    p_value: float | None
    reasons: tuple[str, ...] = field(default=())   # lesbare Begründung, für Mail und UI


def evaluate(window: Window, lam: float | None, history: Sequence[int] | None = None,
             th: Thresholds = DEFAULT, *, history_peak: float | None = None) -> Verdict:
    """Welche Regeln greifen und welche Art Meldung daraus folgt.

    Der bisherige 15-min-Höchstwert kommt entweder aus `history` (Tests, kurze Reihen)
    oder vorberechnet als `history_peak` (Engine, dünn besetzte lange Reihen).
    """
    count15 = sum(window.counts)
    if lam is None:
        return Verdict(frozenset(), "none", count15, None, None, ("Lernphase",))

    expected = WINDOW_BUCKETS * lam
    p = poisson_sf(count15, expected)
    rules: set[str] = set()
    reasons: list[str] = []

    # Kurz nach dem Einschalten einer Log-Quelle ist "nicht gesehen" kein Befund, sondern
    # Bestand (13.10.: Filebeat-Quellen brächten auf einen Schlag Hunderte "neue" Muster).
    # Laufende Schleifen darunter meldet ANHALTEND NEU — in die Tagesübersicht.
    if (not window.seen_before and not window.coverage_unsure
            and (count15 >= th.new_min_count or window.sources >= th.new_min_sources)):
        rules.add("new")
        reasons.append("neues Muster (14 Tage nicht gesehen)")

    if (window.first_seen_hours is not None
            and window.first_seen_hours <= th.persist_max_age_hours
            and window.recent_parts >= PERSIST_PARTS
            and window.recent_total >= th.persist_min_total):
        rules.add("persistent")
        if window.present_at_start or window.coverage_unsure:
            reasons.append(f"anhaltendes Muster seit Beginn der Beobachtung: "
                           f"{window.recent_total} Zeilen in 2 h, in jeder halben Stunde")
        else:
            reasons.append(f"neues Muster läuft seit {window.first_seen_hours:.0f} h anhaltend: "
                           f"{window.recent_total} Zeilen in 2 h, in jeder halben Stunde")

    if window.coverage_unsure:
        # Gerade erst gelesene Quelle: keine Grundlast, keine übliche Breite — SPITZE und
        # AUSBREITUNG wären gegen die Mindest-Grundlast gemessen und träfen jeden Dauerläufer
        # (13.10.: 7 Sofortmails für dockerd "copy stream failed" u. ä. nach der
        # Filebeat-Umstellung, abgefangen von der 30-min-Sperre). Bleibt: ANHALTEND.
        notify = "digest" if "persistent" in rules else "none"
        return Verdict(frozenset(rules), notify, count15, expected, p, tuple(reasons))

    elevated = sum(1 for c in window.counts if c >= th.spike_bucket_factor * lam)
    if history_peak is None:
        sums = sums15(history or [])
        history_peak = max(sums) if sums else 0.0
    # Wiederkehrende Muster (täglicher Lauf, Job) müssen zusätzlich ihr bisheriges
    # Höchstniveau übertreffen — sonst wäre jeder Lauf eine "Spitze", weil der Median
    # eines selten aktiven Musters 0 ist. Ein wiederholter AUSBRUCH (der 401 kam an zwei
    # Tagen) bleibt davon unberührt: seine erste Welle hebt das Perzentil, nicht die Regel.
    recurring = window.active_days >= th.recurring_days
    if (count15 >= th.spike_min_count
            and count15 >= th.spike_factor * expected
            and (not recurring or count15 >= th.recurring_peak_factor * history_peak)
            and p < th.spike_p
            and elevated >= th.spike_buckets_needed):
        rules.add("spike")
        reasons.append(f"{count15} Zeilen in 15 min, erwartet {expected:.1f} "
                       f"({count15 / expected:.0f}×)")

    if (window.entities >= th.spread_min_entities
            and count15 >= th.spread_min_count
            and window.entities >= th.spread_factor * max(window.usual_entities, 1.0)):
        rules.add("spread")
        reasons.append(f"auf {window.entities} Systemen gleichzeitig (Container oder Hosts), üblich "
                       f"{max(window.usual_entities, 1.0):.0f}")

    persistent_now = "persistent" in rules and not window.present_at_start
    if "spread" in rules or persistent_now or (
            "spike" in rules and count15 >= th.spike_immediate_count):
        notify = "immediate"
    elif "spike" in rules:
        notify = "digest"           # deutlich, aber ein Container und überschaubar
    elif "persistent" in rules:
        notify = "digest"           # lief vermutlich schon vor der Beobachtung
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
    #: Höchste 5-min-Rate bei der letzten Mail (0 = noch keine). Bewusst die RATE, nicht
    #: die 15-min-Summe: zu Beginn einer Welle füllt sich das 15-min-Fenster erst
    #: (7 500 → 15 000), das sähe wie eine Verdopplung aus und gäbe eine zweite Mail.
    notified_rate: int
    notified_entities: int
    notified_at: datetime | None = None


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
            recent_mail = (episode.notified_at is not None and
                           now - episode.notified_at < timedelta(minutes=th.renotify_min_minutes))
            if (verdict.notify == "immediate" and not recent_mail
                    and should_renotify(last_count, entities, episode, th)):
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


def should_renotify(rate: int, entities: int, episode: EpisodeState,
                    th: Thresholds = DEFAULT) -> bool:
    """Erneut melden nur, wenn sich die Lage verdoppelt hat — sonst wäre jeder Lauf eine Mail.

    rate: aktuelle 5-min-Menge; verglichen mit der höchsten 5-min-Menge bei der letzten Mail.
    """
    if episode.notified_rate <= 0:
        return True     # bisher nur in der Tagesübersicht → jetzt ist es ein Ausbruch
    if rate >= th.renotify_factor * episode.notified_rate:
        return True
    return (entities >= th.renotify_factor * max(episode.notified_entities, 1)
            and entities - episode.notified_entities >= th.renotify_min_new_entities)
