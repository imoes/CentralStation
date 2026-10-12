"""„Anhaltend neu" und Level-6-Fehlerzeilen — nachgestellt am DocumentIndex-Fall (12.10.2026).

Gemessen in Graylog (2 h): die Content-Store-Retry-Schleife schrieb 1 464 Zeilen
"ERROR com.escenic.indexer.webservice.DocumentIndex: An error occurred …", schubweise
(aktiv in 12 von 24 5-min-Fenstern), aus 1–2 Containern, alle mit Docker-Level 6.
Zwei Lücken ließen das unbemerkt:
  1. der Collector las nur level<=4 — die Zeilen kamen nie an;
  2. NEU greift nur im ersten Fenster eines Musters (≥ 20 in 15 min). Eine leise Schleife
     ist danach "gesehen" und erreicht weder SPITZE noch AUSBREITUNG.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from app.services.error_clusters.collector import (
    BASE_QUERY, ERROR_LINE, BucketTally, TemplateTally, keep_lines,
)
from app.services.error_clusters.engine import BUCKET, MemoryStore, process
from app.services.error_clusters.rules import DEFAULT, Window, evaluate

START = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
DEPLOY = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
KEY = "k_docindex"


def _w(**kw) -> Window:
    base = dict(counts=(0, 0, 12), entities=1, sources=1, usual_entities=1.0, seen_before=True)
    base.update(kw)
    return Window(**base)


# ── Regel ────────────────────────────────────────────────────────────────────

def test_young_steady_pattern_is_reported_immediately():
    v = evaluate(_w(first_seen_hours=3, recent_parts=4, recent_total=1464), 0.1)
    assert "persistent" in v.rules and v.notify == "immediate"
    assert "anhaltend" in v.reasons[0]


def test_not_in_every_half_hour_is_not_persistent():
    v = evaluate(_w(first_seen_hours=3, recent_parts=3, recent_total=1464), 0.1)
    assert "persistent" not in v.rules


def test_too_few_lines_is_not_persistent():
    v = evaluate(_w(first_seen_hours=3, recent_parts=4, recent_total=40), 0.1)
    assert "persistent" not in v.rules


def test_old_pattern_is_not_new():
    """Die nifi-Meldung läuft seit Tagen — sie ist Grundrauschen, nicht neu."""
    v = evaluate(_w(first_seen_hours=96, recent_parts=4, recent_total=960), 0.1)
    assert "persistent" not in v.rules


def test_unknown_age_is_not_new():
    v = evaluate(_w(first_seen_hours=None, recent_parts=4, recent_total=960), 0.1)
    assert "persistent" not in v.rules


def test_pattern_from_start_of_observation_only_goes_to_digest():
    """Erststart / neue Log-Quelle: ob es neu ist, ist nicht entscheidbar — keine Mailflut."""
    v = evaluate(_w(first_seen_hours=3, recent_parts=4, recent_total=960,
                    coverage_unsure=True), 0.1)
    assert "persistent" in v.rules and v.notify == "digest"


def test_learning_phase_still_reports_nothing():
    v = evaluate(_w(first_seen_hours=3, recent_parts=4, recent_total=1464), None)
    assert v.notify == "none"


# ── Engine über die Zeit ─────────────────────────────────────────────────────

def _bucket(b: datetime) -> BucketTally:
    templates: dict[str, TemplateTally] = {}
    # Retry-Schleife: alle 10 min ein Schub von 120 Zeilen, ab dem Deploy.
    if b >= DEPLOY and b.minute % 10 == 0:
        t = TemplateTally("ERROR com.escenic.indexer.webservice.DocumentIndex: An error occurred "
                          "while creating document of object with id:<NUM> of <*>", 120,
                          Counter({"cue-content-store-01.cue.test.example.com": 120}),
                          {"cue-content-store-01.cue.test.example.com"})
        templates[KEY] = t
    return BucketTally(b, templates, Counter(), sum(t.count for t in templates.values()), True)


def test_retry_loop_after_deploy_is_mailed_once_within_two_hours():
    store = MemoryStore(START)
    mails = []
    b = START
    while b < DEPLOY + timedelta(hours=8):
        mails += [e for e in process(store, _bucket(b)) if e.mail]
        b += BUCKET
    assert len(mails) == 1, [(e.action, e.bucket_start, e.verdict.rules) for e in mails]
    m = mails[0]
    # erstes Fenster (120 in 15 min < 20? nein: ≥ 20 → NEU, aber nur Tagesübersicht);
    # die Sofortmail kommt mit "anhaltend", sobald alle vier Halbstunden belegt sind.
    assert "persistent" in m.episode.rules
    assert m.bucket_start <= DEPLOY + timedelta(hours=2)


def test_pattern_present_at_observation_start_is_not_mailed():
    """Gleiche Schleife, läuft aber schon, als die Beobachtung beginnt (Level-6-Umstellung)."""
    store = MemoryStore(START, coverage_since=DEPLOY)
    mails = []
    b = DEPLOY
    while b < DEPLOY + timedelta(hours=8):
        mails += [e for e in process(store, _bucket(b)) if e.mail
                  and "persistent" in e.verdict.rules and e.verdict.notify == "immediate"]
        b += BUCKET
    assert mails == []


# ── Collector ────────────────────────────────────────────────────────────────

def test_query_reads_level6_container_errors_and_keeps_switches_out():
    assert "level:<=4" in BASE_QUERY and "level:6" in BASE_QUERY
    assert "_exists_:container_name" in BASE_QUERY
    assert "NOT source:(nsa* OR nss* OR nsc*)" in BASE_QUERY


def test_error_line_is_case_sensitive():
    assert ERROR_LINE.search("ERROR [2026-10-12 12:24:03,324] com.escenic.indexer.webservice."
                             "DocumentIndex: An error occurred")
    assert ERROR_LINE.search("ERROR - cue.zipline.dcx.dcx_service - Failed to fetch usages")
    assert ERROR_LINE.search('time=1 level=error msg="x"')
    assert ERROR_LINE.search('{"level": "error", "msg": "x"}')
    assert ERROR_LINE.search("Traceback (most recent call last):")
    assert not ERROR_LINE.search("INFO an error occurred, retrying")
    assert not ERROR_LINE.search("WARN errors=0 failures=0")
    assert not ERROR_LINE.search("java.lang.NullPointerException")   # Folgezeile, kein Kopf
    # das erste Level-Wort entscheidet: Info-Meldung über einen Fehlschlag ist kein Fehler
    assert not ERROR_LINE.search("2026-10-12 14:28:18.387  INFO 1 --- [exec-16] "
                                 "m.i.c.i.p.d.p.Yarp3Publisher : YARP3 async feedback "
                                 "PUBLISH for [cueId=7908292], outcome=ERROR")
    assert ERROR_LINE.search("<TS> ERROR [Timer-Driven Process Thread-3] o.a.nifi INFO x")


def test_new_at_observation_start_is_digest_with_honest_reason():
    """Erststart einer Quelle: 600 Zeilen eines vorher ungelesenen Musters sind kein
    "14 Tage nicht gesehen" und keine Sofortmail."""
    v = evaluate(_w(counts=(200, 200, 200), seen_before=False, coverage_unsure=True), 0.1)
    assert "new" in v.rules and v.notify == "digest"
    assert "erst seit Kurzem" in v.reasons[0]
    v = evaluate(_w(counts=(200, 200, 200), seen_before=False), 0.1)
    assert "14 Tage" in v.reasons[0]


def test_keep_lines_filters_level6_and_drops_docker_twins():
    msgs = [
        {"level": 3, "container_name": "a", "message": "ERROR boom"},
        {"level": 6, "container_name": "a", "message": "ERROR boom"},          # Zwilling
        {"level": 6, "container_name": "b", "message": "ERROR other"},         # echt
        {"level": 6, "container_name": "b", "message": "INFO error count 0"},  # Rauschen
        {"level": 6, "container_name": "b", "message": "x\nERROR only later"}, # nur 1. Zeile zählt
        {"level": 4, "source": "host", "message": "kernel: oops"},             # ≤4 immer
    ]
    kept = keep_lines(msgs)
    assert [(m["level"], m["message"]) for m in kept] == [
        (3, "ERROR boom"), (6, "ERROR other"), (4, "kernel: oops")]


def test_default_thresholds():
    assert DEFAULT.persist_max_age_hours == 24
    assert DEFAULT.persist_min_total == 100
    assert DEFAULT.persist_window_buckets == 24
