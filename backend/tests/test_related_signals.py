"""Recherche: nur Signale mit belegtem Zusammenhang (03.11.2026).

Anlass: rhsmcertd-JSON-Fehler auf 13 RHEL-Hosts. Die Mail nannte sie "13 Container" und
führte "File group PrintPdfExport" (1 von 13 Hosts) als verwandtes Signal — die KI schloss
daraus auf fehlende Konfigurationsdateien. Gleichzeitigkeit ist kein Zusammenhang.
"""
from __future__ import annotations

from app.services.error_clusters.notify import hypergeom_sf, related_alerts
from app.services.error_clusters.runner import entity_kind

N = 3093                 # CheckMK-Hosts
AFFECTED = 13


def _svc(affected: int, elsewhere: int) -> dict:
    hosts = {f"a{i}" for i in range(affected)} | {f"x{i}" for i in range(elsewhere)}
    return {"hosts": hosts, "affected": {f"a{i}" for i in range(affected)},
            "first": {"host": "a0", "titel": "t", "schwere": "critical", "zeit": "z"}}


def test_hypergeometric_tail():
    assert hypergeom_sf(0, 100, 5, 10) == 1.0
    # 1 von 13 betroffenen bei 1 Treffer in 3093: P = 13/3093
    assert abs(hypergeom_sf(1, N, 1, AFFECTED) - AFFECTED / N) < 1e-12


def test_coincidence_on_one_of_thirteen_hosts_is_not_related():
    """File group PrintPdfExport: nur auf cue0110 — gleichzeitig, aber ohne Beleg."""
    related, hidden = related_alerts({"File group PrintPdfExport": _svc(1, 0)}, AFFECTED, N)
    assert related == [] and hidden == 1


def test_alert_on_most_affected_hosts_and_rare_elsewhere_is_related():
    related, hidden = related_alerts({"Systemd Service Summary": _svc(9, 2)}, AFFECTED, N)
    assert [r["dienst"] for r in related] == ["Systemd Service Summary"] and hidden == 0
    assert related[0]["grund"].startswith("auf 9 von 13 betroffenen Hosts, sonst auf 2 von 3080")


def test_alert_that_is_everywhere_is_not_related():
    """Check_MK-Rauschen auf 1 400 Hosts: auch auf 3 betroffenen ist es kein Befund."""
    related, _ = related_alerts({"Check_MK": _svc(3, 1400)}, AFFECTED, N)
    assert related == []


def test_single_host_outbreak_keeps_a_rare_alert_on_that_host():
    """OOM auf einem Host: ein Alert, der sonst nirgends auftritt, gehört dazu."""
    related, _ = related_alerts({"Memory": _svc(1, 0)}, 1, N)
    assert [r["dienst"] for r in related] == ["Memory"]


def test_entity_kind_hosts_vs_containers():
    hosts = {"cue0111.example.com": 12, "cue0109.example.com": 12}
    assert entity_kind(hosts, list(hosts)) == "Hosts"                     # Filebeat/Syslog
    assert entity_kind({"cue-zipline-dam": 9}, ["docker0218.example.com"]) == "Container"
    assert entity_kind({"a": 1, "h1": 1}, ["h1"]) == "Container und Hosts"
