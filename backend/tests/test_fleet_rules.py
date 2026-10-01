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
