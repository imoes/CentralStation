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


def test_pattern_at_observation_start_is_not_new():
    """Neue Log-Quelle: was gleich beim Einschalten läuft, ist Bestand, nicht "neu"."""
    v = evaluate(_w(counts=(200, 200, 200), seen_before=False, coverage_unsure=True), 0.1)
    assert "new" not in v.rules
    v = evaluate(_w(counts=(200, 200, 200), seen_before=False), 0.1)
    assert "new" in v.rules and "14 Tage" in v.reasons[0]


def test_beats_lines_are_kept_by_log_level():
    """Filebeat liefert kein `level`, nur das geschätzte log_level (13.10., docker0209)."""
    msgs = [
        {"source": "docker0209.example.com", "log_level": "critical",
         "message": "sammelpdf kernel: Out of memory: Killed process 2046866 ([celeryd: celer)"},
        {"source": "docker0209.example.com", "log_level": "error",
         "message": 'sammelpdf dockerd[1250]: level=error msg="copy stream failed"'},
        {"source": "docker0209.example.com", "log_level": "info",
         "message": "sammelpdf kernel: Mem-Info:"},
        {"source": "docker0209.example.com", "log_level": "warn", "message": "x warning"},
        {"source": "docker0209.example.com", "message": "no level at all"},
    ]
    kept = keep_lines(msgs)
    assert [m["log_level"] for m in kept] == ["critical", "error"]
    assert "log_level:(emerg OR alert OR critical OR error)" in BASE_QUERY
    assert "NOT _exists_:level" in BASE_QUERY


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


def test_fresh_source_triggers_no_spike_or_spread():
    """13.10.: nach der Filebeat-Umstellung galten Dauerläufer als SPITZE/AUSBREITUNG."""
    w = _w(counts=(3000, 3000, 3000), entities=30, seen_before=False, coverage_unsure=True)
    v = evaluate(w, DEFAULT.lam_floor)
    assert v.rules == frozenset() and v.notify == "none"
    w = _w(counts=(3000, 3000, 3000), entities=30, coverage_unsure=True,
           first_seen_hours=2, recent_parts=4, recent_total=50000)
    v = evaluate(w, DEFAULT.lam_floor)
    assert v.rules == frozenset({"persistent"}) and v.notify == "digest"
    # dieselben Zahlen bei bekannter Quelle: sofort
    w = _w(counts=(3000, 3000, 3000), entities=30, usual_entities=1.0)
    assert evaluate(w, DEFAULT.lam_floor).notify == "immediate"


def test_alarm_from_fresh_source_is_not_resent_after_restart():
    """13.10.: Fehlalarme der Filebeat-Umstellung dürfen auch nach Neustart nicht raus."""
    from types import SimpleNamespace as NS
    from app.services.error_clusters.notify import from_fresh_source
    cov = datetime(2026, 10, 13, 11, 35, tzinfo=timezone.utc)
    now = cov + timedelta(minutes=40)
    fresh = NS(first_seen=datetime(2026, 10, 13, 11, 40, tzinfo=timezone.utc))
    old = NS(first_seen=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    assert from_fresh_source(NS(source="log"), fresh, [cov], now) is True
    assert from_fresh_source(NS(source="log"), old, [cov], now) is False
    assert from_fresh_source(NS(source="checkmk_fleet"), None, [cov], now) is False
    assert from_fresh_source(NS(source="log"), fresh, [], now) is False


def test_only_the_two_hours_after_coverage_start_are_unsure():
    from app.services.error_clusters.engine import fresh_at_coverage
    cov = datetime(2026, 10, 13, 11, 35, tzinfo=timezone.utc)
    now = cov + timedelta(hours=3)
    assert fresh_at_coverage(cov, [cov], now) is True
    assert fresh_at_coverage(cov + timedelta(hours=1, minutes=59), [cov], now) is True
    assert fresh_at_coverage(cov + timedelta(hours=2), [cov], now) is False
    assert fresh_at_coverage(cov - timedelta(minutes=5), [cov], now) is False   # schon bekannt
    assert fresh_at_coverage(None, [cov], now) is False


def test_every_earlier_source_switch_counts_not_only_the_newest():
    """13.10.: die Level-6-Muster vom 12.10. galten nach der Filebeat-Umstellung als "neu
    seit 24 h" — die Erkennung kannte nur noch den neuesten Quellenstart."""
    from app.services.error_clusters.engine import fresh_at_coverage
    level6 = datetime(2026, 10, 12, 12, 25, tzinfo=timezone.utc)
    beats = datetime(2026, 10, 13, 11, 35, tzinfo=timezone.utc)
    nifi = datetime(2026, 10, 12, 12, 25, tzinfo=timezone.utc)
    assert fresh_at_coverage(nifi, [level6, beats], datetime(2026, 10, 13, 12, 5,
                                                             tzinfo=timezone.utc)) is True


def test_unsure_expires_once_a_baseline_exists():
    """Ohne Ablauf meldeten Muster aus den 2 h nach einer Umstellung NIE wieder eine Spitze."""
    from app.services.error_clusters.engine import fresh_at_coverage
    cov = datetime(2026, 10, 13, 11, 35, tzinfo=timezone.utc)
    first = cov + timedelta(minutes=5)
    assert fresh_at_coverage(first, [cov], cov + timedelta(hours=23)) is True
    assert fresh_at_coverage(first, [cov], cov + timedelta(hours=24)) is False


def test_pattern_present_at_start_is_never_mailed_as_persistent_new():
    """Läuft ein Muster schon beim Einschalten, ist es nach Ablauf der Unsicherheit (24 h)
    Bestand — "anhaltend neu" bleibt Tagesübersicht (Replay: Rauschen ab Erkennungsstart)."""
    v = evaluate(_w(first_seen_hours=24, recent_parts=4, recent_total=1800,
                    present_at_start=True), 50.0)
    assert "persistent" in v.rules and v.notify == "digest"


# ── Format des Versenders darf kein "neues Muster" erzeugen (13.10. 13:55) ────────

def test_syslog_head_is_stripped_so_format_changes_do_not_create_new_patterns():
    from app.services.error_clusters.collector import _line
    old = _line({"message": "2026-10-13T13:43:03.92+02:00 cue0141 dockerd[1250]: copy stream failed",
                 "source": "cue0141.example.com", "log_level": "error"})
    new = _line({"message": "cue0141 dockerd[1250]: copy stream failed",
                 "source": "cue0141.example.com", "log_level": "error"})
    other_host = _line({"message": "cue0200 dockerd[77]: copy stream failed",
                        "source": "cue0200.example.com", "log_level": "error"})
    assert old.message == new.message == "dockerd[1250]: copy stream failed"
    assert other_host.message.startswith("dockerd[")              # ein Muster über Hosts
    # docker0209 schreibt "sammelpdf" — Filebeat-Zeile: Kopf trotzdem weg
    assert _line({"message": "sammelpdf kernel: Out of memory: Killed process 1",
                  "source": "docker0209.example.com", "log_level": "critical"}).message \
        == "kernel: Out of memory: Killed process 1"


def test_application_text_is_not_mistaken_for_a_syslog_head():
    from app.services.error_clusters.collector import _line
    for m in ({"message": "ERROR com.escenic.x: boom", "source": "h", "level": 3},
              {"message": "ERROR com.escenic.x: boom", "source": "h", "level": 6,
               "container_name": "cue-content-store-01"}):
        assert _line(m).message == "ERROR com.escenic.x: boom"


def test_burst_threshold_above_normal_maximum():
    """Normalbetrieb 05.–12.10.: max. 46 neue Muster je Fenster; Rollout 13.10.: 480."""
    assert 46 < DEFAULT.burst_new_templates < 74
