"""Regeln der Fehler-Cluster-Erkennung, nachgestellt am gemessenen 401-Vorfall.

Die Zahlen stammen aus Graylog (01.10.2026):
  * Rauschen: cue-zipline-dam.cue.stage, ~5 400 Fehler je 3 h ≈ 150 je 5 min, konstant.
  * Welle 1 (30.09. 11:30): ein nie gesehenes Muster, ~15 000 Zeilen in 10 min, 26 Container.
  * Welle 2 (01.10. 08:00, ~20 h später): dasselbe Muster, 24 Container.
Beide Wellen müssen sofort melden, das Rauschen nie.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import statistics

from app.services.error_clusters.rules import (
    DEFAULT,
    median_with_zeros,
    quantile_with_zeros,
    EpisodeState,
    Window,
    baseline,
    evaluate,
    fleet_cluster,
    next_action,
    poisson_sf,
    quantile,
    should_renotify,
    sums15,
)

NOW = datetime(2026, 10, 1, 8, 10, tzinfo=timezone.utc)
DAY = timedelta(days=1)
DAY_BUCKETS = 288


def _w(counts, entities=1, sources=1, usual=1.0, seen=True) -> Window:
    return Window(tuple(counts), entities, sources, usual, seen)


# ── Statistik ────────────────────────────────────────────────────────────────

def test_poisson_tail_basics():
    assert poisson_sf(0, 3.0) == 1.0
    assert poisson_sf(2, 3.0) == 1.0                  # unter dem Erwartungswert: nicht signifikant
    assert poisson_sf(10, 0.0) == 0.0
    # P(X≥10 | 3) ≈ 0.00110 (Tabellenwert)
    assert poisson_sf(10, 3.0) == pytest.approx(0.0011025, rel=1e-3)


def test_poisson_tail_survives_huge_counts():
    """15 000 Zeilen bei Erwartung 0,6 — darf weder überlaufen noch hängen."""
    p = poisson_sf(15_000, 0.6)
    assert p == 0.0 or p < 1e-300


def test_quantile_and_sums():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert sums15([1, 2, 3, 4]) == [6, 9]


# ── Grundlast ────────────────────────────────────────────────────────────────

def test_warmup_reports_nothing():
    assert baseline([0] * 100, timedelta(hours=10)) is None


def test_median_baseline_includes_zeros_and_has_a_floor():
    assert baseline([0] * 500, 2 * DAY) == DEFAULT.lam_floor
    assert baseline([150] * 500, 2 * DAY) == 150


def test_median_is_not_poisoned_by_an_earlier_wave():
    """Welle 1 liegt in der Historie — die Grundlast für Welle 2 muss trotzdem ~0 bleiben."""
    history = [0] * (2 * DAY_BUCKETS)
    history[-240:-230] = [7_500] * 10            # Welle 1, ~20 h vor dem Bewertungsfenster
    assert baseline(history, 3 * DAY) == DEFAULT.lam_floor


def test_seasonal_baseline_after_14_days():
    lam = baseline([0] * 100, 20 * DAY, same_hour_of_week=[1200, 1200, 1200])
    assert lam == 100          # 1200 je Stunde = 100 je 5 min


# ── Der Vorfall ──────────────────────────────────────────────────────────────

def test_noise_container_never_alerts():
    """dam.stage: konstant ~150 je 5 min — das ist seine Grundlast, kein Vorfall."""
    history = [150] * (3 * DAY_BUCKETS)
    lam = baseline(history, 3 * DAY)
    v = evaluate(_w([152, 148, 155], entities=1, usual=1.0, seen=True), lam, history)
    assert v.notify == "none"
    assert v.rules == frozenset()


def test_wave_one_new_pattern_on_26_containers_alerts_immediately():
    history = [0] * (2 * DAY_BUCKETS)            # Muster gab es nie
    lam = baseline(history, 2 * DAY)
    v = evaluate(_w([0, 7_300, 7_500], entities=26, sources=3, usual=0.0, seen=False),
                 lam, history)
    assert {"new", "spread"} <= v.rules
    assert v.notify == "immediate"


def test_wave_two_twenty_hours_later_is_a_spike_and_spread():
    history = [0] * (3 * DAY_BUCKETS)
    history[-240:-230] = [7_500] * 10            # Welle 1 in der Historie
    lam = baseline(history, 3 * DAY)
    v = evaluate(_w([0, 5_990, 14_578], entities=24, sources=3, usual=0.0, seen=True),
                 lam, history)
    assert {"spike", "spread"} <= v.rules
    assert "new" not in v.rules                  # gesehen: es ist eine Wiederholung
    assert v.notify == "immediate"


def test_ten_thousand_identical_lines_are_a_spike_not_one_entry():
    """Der Unterschied zum Feed-Pfad: dort wird pro dedup_key EINE Meldung behalten.
    Hier zählt jede Zeile — 10 000 gleiche Zeilen sind ein Ausbruch."""
    history = [2] * (2 * DAY_BUCKETS)
    lam = baseline(history, 2 * DAY)
    v = evaluate(_w([3_000, 3_500, 3_500], entities=1, seen=True), lam, history)
    assert v.count15 == 10_000
    assert "spike" in v.rules and v.notify == "immediate"


def test_quiet_new_pattern_without_spike_is_digest_only():
    history = [0] * (2 * DAY_BUCKETS)
    v = evaluate(_w([7, 7, 7], entities=1, sources=1, seen=False),
                 baseline(history, 2 * DAY), history)
    # 21 Zeilen: NEU (≥ 20), aber unter der Spitzen-Mindestmenge 50 → Tagesübersicht
    assert v.rules == frozenset({"new"})
    assert v.notify == "digest"


def test_single_burst_bucket_is_not_a_spike():
    """Ein einzelnes lautes Fenster in drei reicht nicht (2 von 3 verlangt)."""
    history = [5] * (2 * DAY_BUCKETS)
    v = evaluate(_w([5, 5, 400], entities=1), baseline(history, 2 * DAY), history)
    assert "spike" not in v.rules


def test_spread_needs_many_more_containers_than_usual():
    history = [10] * (2 * DAY_BUCKETS)
    lam = baseline(history, 2 * DAY)
    assert "spread" not in evaluate(_w([40, 40, 40], entities=10, usual=5.0), lam, history).rules
    assert "spread" in evaluate(_w([40, 40, 40], entities=16, usual=5.0), lam, history).rules


def test_spread_needs_volume():
    """Backtest: 8–24 Zeilen auf 5 Quellen (systemd-Meldungen) waren eine Sofort-Mail."""
    history = [0] * (2 * DAY_BUCKETS)
    lam = baseline(history, 2 * DAY)
    assert "spread" not in evaluate(_w([3, 3, 4], entities=10), lam, history).rules


def test_recurring_pattern_must_beat_its_own_peak():
    """Täglich wiederkehrend (systemd/GnuPG): so laut wie an den Vortagen → keine Spitze."""
    history = [0] * (5 * DAY_BUCKETS)
    for d in range(5):
        history[d * DAY_BUCKETS + 100: d * DAY_BUCKETS + 103] = [150, 150, 150]
    lam = baseline(history, 5 * DAY)
    w = Window((150, 150, 150), 1, 1, 1.0, True, active_days=5)
    assert "spike" not in evaluate(w, lam, history).rules
    louder = Window((900, 900, 900), 1, 1, 1.0, True, active_days=5)   # 2 700 ≥ 2 × 450
    assert "spike" in evaluate(louder, lam, history).rules


def test_repeated_outbreak_is_still_a_spike():
    """Der 401 kam an zwei Tagen. Nur zwei aktive Tage → kein "wiederkehrendes" Muster;
    die erste Welle in der Historie darf die zweite nicht verdecken."""
    history = [0] * (3 * DAY_BUCKETS)
    history[-240:-230] = [7_500] * 10
    lam = baseline(history, 3 * DAY)
    w = Window((0, 5_990, 14_578), 1, 1, 1.0, True, active_days=1)
    assert "spike" in evaluate(w, lam, history).rules


def test_single_container_spike_goes_to_digest_unless_huge():
    history = [2] * (2 * DAY_BUCKETS)
    lam = baseline(history, 2 * DAY)
    assert evaluate(_w([100, 100, 100]), lam, history).notify == "digest"
    assert evaluate(_w([400, 400, 400]), lam, history).notify == "immediate"


def test_reasons_are_readable():
    history = [0] * (2 * DAY_BUCKETS)
    v = evaluate(_w([0, 7_300, 7_500], entities=26, sources=3, seen=False),
                 baseline(history, 2 * DAY), history)
    text = " ".join(v.reasons)
    assert "neues Muster" in text and "auf 26 Systemen" in text


# ── Episoden ─────────────────────────────────────────────────────────────────

def _act(verdict, episode=None, *, muted=False, closed=None, lam=0.2, last=7_500, ent=26):
    return next_action(verdict=verdict, episode=episode, muted=muted, last_closed_at=closed,
                       now=NOW, lam=lam, last_count=last, entities=ent)


def _immediate(count=15_000):
    history = [0] * (2 * DAY_BUCKETS)
    return evaluate(_w([0, count // 2, count // 2], entities=26, seen=False),
                    baseline(history, 2 * DAY), history)


def test_first_outbreak_opens_an_episode():
    assert _act(_immediate()) == "open"


def test_ongoing_outbreak_does_not_mail_every_run():
    ep = EpisodeState(NOW - timedelta(minutes=10), NOW - timedelta(minutes=5), 7_500, 26)
    assert _act(_immediate(15_000), ep, last=7_500) == "update"


def test_filling_window_is_not_a_doubling():
    """Welle beginnt: 15-min-Summe 7 500 → 15 000, die Rate bleibt 7 500 je 5 min."""
    ep = EpisodeState(NOW - timedelta(minutes=5), NOW - timedelta(minutes=5), 7_500, 26)
    assert _act(_immediate(15_000), ep, last=7_500) == "update"


def test_doubling_renotifies():
    ep = EpisodeState(NOW - timedelta(minutes=30), NOW - timedelta(minutes=5), 3_000, 26)
    assert _act(_immediate(15_000), ep, last=7_500) == "renotify"


def test_episode_closes_after_an_hour_of_quiet():
    ep = EpisodeState(NOW - timedelta(hours=2), NOW - timedelta(minutes=61), 15_000, 26)
    quiet = evaluate(_w([0, 0, 0]), 0.2, [0] * 600)
    assert _act(quiet, ep, last=0) == "close"
    ep_recent = EpisodeState(NOW - timedelta(hours=2), NOW - timedelta(minutes=20), 15_000, 26)
    assert _act(quiet, ep_recent, last=0) == "update"


def test_cooldown_reopens_instead_of_new_episode():
    assert _act(_immediate(), closed=NOW - timedelta(hours=1)) == "reopen"
    assert _act(_immediate(), closed=NOW - timedelta(hours=4)) == "open"


def test_muted_pattern_never_acts():
    assert _act(_immediate(), muted=True) == "none"


def test_digest_episode_upgrades_to_mail_on_outbreak():
    """Bisher nur in der Tagesübersicht (keine Mail) → jetzt Ausbruch → melden."""
    ep = EpisodeState(NOW - timedelta(hours=1), NOW - timedelta(minutes=5), 0, 0)
    assert should_renotify(7_500, 26, ep)


def test_fleet_rule():
    assert fleet_cluster(8, 1.0)
    assert not fleet_cluster(4, 0.0)
    assert not fleet_cluster(8, 4.0)


@pytest.mark.parametrize("series", [
    [0] * 10, [0] * 9 + [5], [0, 0, 3, 7, 9], [4, 4, 4, 4], [0, 1, 0, 1, 0, 1], [0, 2, 9, 3],
])
def test_sparse_median_and_quantile_equal_the_full_computation(series):
    nonzero = [v for v in series if v]
    assert median_with_zeros(nonzero, len(series)) == statistics.median(series)
    for q in (0.5, 0.9, 0.995):
        assert quantile_with_zeros(nonzero, len(series), q) == pytest.approx(quantile(series, q))
