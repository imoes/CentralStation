"""Die Engine über drei Tage, in der gemessenen Form des 401-Vorfalls.

Kein Graylog, kein Drain3: die Zählungen werden direkt als Fenster eingespeist. Das
prüft das Zusammenspiel von Grundlast, Regeln und Episoden über die Zeit — genau die
Stelle, an der ein gleitender Mittelwert Welle 2 verpasst hätte.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from app.services.error_clusters.collector import BucketTally, TemplateTally
from app.services.error_clusters.engine import BUCKET, MemoryStore, process

START = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
WAVE1 = (datetime(2026, 9, 30, 11, 30, tzinfo=timezone.utc), datetime(2026, 9, 30, 12, 20, tzinfo=timezone.utc))
WAVE2 = (datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc), datetime(2026, 10, 1, 8, 40, tzinfo=timezone.utc))
END = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

NOISE, LOG401, OTHER = "k_noise", "k_401", "k_other"


def _tt(template: str, count: int, containers: list[str], source: str = "cueservices") -> TemplateTally:
    t = TemplateTally(template, count, Counter(), {source}, [template])
    per = max(1, count // max(1, len(containers)))
    for c in containers:
        t.entities[c] = per
    return t


def _bucket(b: datetime) -> BucketTally:
    templates = {
        NOISE: _tt("dam stage noise <*>", 150, ["cue-zipline-dam.cue.stage"]),
        OTHER: _tt("occasional <*>", 2 if b.minute % 15 == 0 else 0, ["svc-a"]),
    }
    if not templates[OTHER].count:
        del templates[OTHER]
    for lo, hi, n in ((*WAVE1, 26), (*WAVE2, 24)):
        if lo <= b < hi:
            containers = [f"cue-zipline-product-{i}" for i in range(n)]
            templates[LOG401] = _tt("Failed to read change-log: HTTP401 Unauthorized'", 7_500, containers)
    entities = Counter()
    for t in templates.values():
        entities.update(t.entities)
    return BucketTally(b, templates, entities, sum(t.count for t in templates.values()), True)


def _replay():
    store = MemoryStore(START)
    events = []
    b = START
    while b < END:
        events.extend(process(store, _bucket(b)))
        b += BUCKET
    return store, events


def test_both_waves_mail_within_ten_minutes_and_noise_stays_silent():
    store, events = _replay()
    mails = [e for e in events if e.mail]
    keys = {e.template.key for e in mails}
    assert NOISE not in keys
    assert OTHER not in keys

    wave_mails = [e for e in mails if e.template.key == LOG401]
    assert len(wave_mails) == 2, [(e.action, e.bucket_start) for e in wave_mails]
    first, second = wave_mails
    assert WAVE1[0] <= first.bucket_start <= WAVE1[0] + timedelta(minutes=10)
    assert WAVE2[0] <= second.bucket_start <= WAVE2[0] + timedelta(minutes=10)
    # Gemeldet wird schon im ERSTEN Fenster der Welle — dort greift die Ausbreitung
    # (24–26 Container). SPITZE braucht zwei erhöhte Fenster und kommt 5 min später dazu.
    assert "spread" in first.verdict.rules
    assert "spread" in second.verdict.rules
    second_ep = next(e for e in store.closed if e.id == second.episode.id)
    assert {"spike", "spread"} <= second_ep.rules
    assert "new" not in second_ep.rules          # Wiederholung, kein neues Muster


def test_each_wave_is_one_episode_that_closes():
    store, events = _replay()
    episodes = [e for e in store.closed if e.key == LOG401]
    assert len(episodes) == 2
    assert all(e.closed_at is not None for e in episodes)
    assert episodes[0].total >= 10 * 7_500 // 2


def test_no_mail_storm_during_a_wave():
    """Eine Welle von 50 Minuten = 10 Läufe. Gemeldet wird einmal, nicht zehnmal."""
    _, events = _replay()
    in_wave1 = [e for e in events if e.mail and WAVE1[0] <= e.bucket_start < WAVE1[1] + timedelta(hours=1)]
    assert len(in_wave1) == 1


def test_episode_without_known_template_does_not_break_the_run():
    """Ein Lauf darf nicht an einer einzelnen fehlerhaften Episode scheitern."""
    from app.services.error_clusters.engine import Episode
    store = MemoryStore(START)
    store.open["unknown"] = Episode(id="x", key="unknown", started_at=START, last_active_at=START)
    process(store, _bucket(START))          # darf keinen KeyError werfen
