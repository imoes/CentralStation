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


def test_capped_window_is_recounted_exactly():
    """> 80 000 Zeilen in 5 min: die Stichprobe zählt zu wenig — Graylog zählt nach."""
    import asyncio
    from app.services.error_clusters.runner import _recount_exact

    seen = {}

    class FakeGraylog:
        async def count_occurrences(self, specs):
            seen["specs"] = specs
            return {s["id"]: {"count": 90_000, "containers": [{"name": "c1", "count": 50_000},
                                                              {"name": "c2", "count": 40_000}]}
                    for s in specs}

    t = _bucket(WAVE1[0])
    t.templates[LOG401].count = 9_000                 # Stichprobe
    asyncio.run(_recount_exact(FakeGraylog(), t, WAVE1[0]))
    assert t.templates[LOG401].count == 90_000
    assert set(t.templates[LOG401].entities) == {"c1", "c2"}
    assert any('message:"' in s["query"] and "level:<=4" in s["query"] for s in seen["specs"])



def test_weekly_job_is_normal_in_its_hour_of_week():
    """Batchlauf jeden Montag 09:00–09:15 mit 3 000 Zeilen: an 2–3 Tagen in 14 aktiv, also
    kein "wiederkehrendes" Muster nach Tagen — erst die Grundlast derselben Wochenstunde
    macht ihn normal. Ohne sie wäre er jeden Montag eine Spitze."""
    from app.services.error_clusters.collector import BucketTally, TemplateTally
    from app.services.error_clusters.engine import BUCKET, MemoryStore, process
    start = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)          # ein Montag
    store = MemoryStore(start)
    mails = []
    b = start
    while b < start + timedelta(days=22):
        templates = {}
        if b.weekday() == 0 and b.hour == 9 and b.minute < 15:
            templates["weekly"] = TemplateTally("nightly batch <*> failed rows", 1_000,
                                                Counter({"batch-runner": 1_000}), {"batchhost"},
                                                ["x"])
        ev = process(store, BucketTally(b, templates, Counter(), 0, True))
        mails += [(e.bucket_start, e.action) for e in ev if e.mail and e.template.key == "weekly"]
        b += BUCKET
    third_monday = start + timedelta(days=21)
    assert not [m for m in mails if m[0] >= third_monday], mails
    assert store.hourly["weekly"][start.replace(hour=9)] == 3_000
