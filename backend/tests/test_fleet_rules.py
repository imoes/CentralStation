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
