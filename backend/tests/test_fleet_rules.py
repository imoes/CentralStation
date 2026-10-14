"""CheckMK-Flotten-Cluster: derselbe Service auf vielen Hosts — gegen das Übliche."""
from __future__ import annotations

from app.services.error_clusters.fleet import SKIP_SERVICES, evaluate, fleet_key

N = 7 * 96          # 15-min-Fenster in 7 Tagen


def _hosts(n: int) -> set[str]:
    return {f"web{i:02d}.example" for i in range(n)}


def test_rare_service_failing_on_many_hosts_is_a_fleet_cluster():
    hits = evaluate({"NTP Time": _hosts(12)}, {"NTP Time": [1, 2, 1]}, N,
                    {"NTP Time": [("cmk:a:NTP Time", "high")]})
    assert [h.service for h in hits] == ["NTP Time"]
    assert len(hits[0].hosts) == 12


def test_churny_service_at_its_usual_level_is_not():
    """"Check_MK" schwankt ständig (542 Hosts in 7 Tagen) — 12 Hosts sind dort normal."""
    history = [12] * N            # in jedem Fenster ~12 Hosts
    assert evaluate({"Check_MK": _hosts(12)}, {"Check_MK": history}, N, {}) == []
    assert evaluate({"Check_MK": _hosts(40)}, {"Check_MK": history}, N, {})   # 3× üblich


def test_few_hosts_are_never_a_fleet():
    assert evaluate({"NTP Time": _hosts(4)}, {}, N, {}) == []


def test_skipped_services_never_count():
    for svc in SKIP_SERVICES:
        assert evaluate({svc: _hosts(50)}, {}, N, {}) == []


def test_fleet_key_is_stable_and_distinct():
    assert fleet_key("NTP Time") == fleet_key("NTP Time")
    assert fleet_key("NTP Time") != fleet_key("Memory")


# ── Eine Mail je Vorfall (01./02.10.: 16 Mails für ~8 Vorfälle) ─────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.services.error_clusters.fleet import group_leader, worth_renotify  # noqa: E402

NOW = datetime(2026, 10, 1, 22, 37, tzinfo=timezone.utc)
AIM = {"aim014", "aim03", "aim07", "aim09", "aomp05-v"}


def test_simultaneous_services_on_the_same_hosts_are_one_incident():
    """ORD1 auf 6 Hosts wurde gemeldet; ORD2/ORD3 auf 5 davon gehören dazu."""
    cands = [("inc-ord1", NOW, AIM | {"aim04"})]
    assert group_leader(AIM, cands, NOW) == "inc-ord1"


def test_unrelated_hosts_stay_separate():
    """23:01 Storage Pool (vpp*) und VM-Backup-Status (dxu*): zwei echte Vorfälle."""
    cands = [("inc-vpp", NOW, {"vpp0199", "vpp0204", "vpp0210"})]
    assert group_leader({"dxu17", "dxu23", "dxu27"}, cands, NOW) is None


def test_old_outbreak_does_not_absorb_a_new_one():
    cands = [("inc-old", NOW - timedelta(minutes=45), AIM)]
    assert group_leader(AIM, cands, NOW) is None


def test_unreported_candidate_is_no_leader():
    assert group_leader(AIM, [(None, NOW, AIM)], NOW) is None


def test_best_overlap_wins():
    cands = [("inc-a", NOW, {"aim03", "x1", "x2", "x3"}), ("inc-b", NOW, AIM)]
    assert group_leader(AIM, cands, NOW) == "inc-b"


def test_renotify_only_when_hosts_double_and_at_most_hourly():
    assert not worth_renotify(30, 31, NOW - timedelta(hours=5), NOW)   # gleiche Lage
    assert worth_renotify(30, 14, NOW - timedelta(hours=2), NOW)       # verdoppelt
    assert not worth_renotify(30, 14, NOW - timedelta(minutes=20), NOW)  # zu kurz her


# ── Wiederkehrend zur gleichen Uhrzeit (14.10.: Orderserver jede Nacht) ─────────

from app.services.error_clusters.fleet import recurring_reason  # noqa: E402
from app.services.error_clusters.rules import DEFAULT  # noqa: E402


def test_daily_job_is_a_fleet_hit_but_marked_recurring():
    """ORD2 jede Nacht 00:30–05:00: der 7-Tage-Median ist 0, die Flotten-Regel greift —
    erst der Tagesvergleich erkennt den geplanten Lauf."""
    svc = "Process orderserver ORD2"
    hits = evaluate({svc: _hosts(5)}, {svc: [5] * 7}, N, {}, recurring={svc: 6})
    assert [h.service for h in hits] == [svc]
    assert hits[0].recurring() is True
    assert "6 der letzten 7 Tage" in recurring_reason(hits[0])


def test_first_nights_of_a_new_failure_are_still_reported():
    """Ein neuer Ausfall, der erst zweimal zur gleichen Zeit kam, ist noch kein Job."""
    svc = "Process orderserver ORD1"
    hit = evaluate({svc: _hosts(7)}, {}, N, {}, recurring={svc: DEFAULT.fleet_recurring_days - 1})[0]
    assert hit.recurring() is False
    assert evaluate({svc: _hosts(7)}, {}, N, {})[0].recurring_days == 0   # ohne Angabe: neu


def test_recurring_defaults():
    assert (DEFAULT.fleet_recurring_days, DEFAULT.fleet_recurring_lookback_days,
            DEFAULT.fleet_recurring_tolerance_minutes) == (3, 7, 30)


def test_a_day_counts_only_if_similar_in_size_and_quiet_otherwise():
    """Backtest 14 Tage: ohne diese Bedingungen galten 12 von 15 Check_MK-Ausfällen als
    wiederkehrend (30.09.–02.10. ganztägig gestört), mit ihnen 0."""
    from app.services.error_clusters.fleet import recurring_days
    # Orderserver: jede Nacht 6–7 Hosts im Fenster, sonst nichts → 7 Tage
    assert recurring_days([(7, 0)] * 7, hosts_now=7) == 7
    # Check_MK: im Fenster 31 Hosts, aber über den Tag 40 große Fenster → kein Job
    assert recurring_days([(31, 40)] * 7, hosts_now=31) == 0
    # Im Fenster nur 6 Hosts, heute 31 → nicht ähnlich groß
    assert recurring_days([(6, 0)] * 7, hosts_now=31) == 0
    # Mindestens fleet_min_hosts, auch wenn heute klein
    assert recurring_days([(4, 0)] * 7, hosts_now=5) == 0
    # bis zu 2 große Fenster außerhalb sind erlaubt (z. B. Wiederholungsversuch)
    assert recurring_days([(5, 2), (5, 3)], hosts_now=5) == 1
