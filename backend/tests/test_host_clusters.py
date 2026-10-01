"""host-Incidents in der Fehler-Cluster-Erkennung (vorher incident/correlator.py).

Die Fälle sind unverändert übernommen — das Verhalten des abgelösten Correlators gilt
weiter, nur an der neuen Stelle.
"""
from app.services.error_clusters.incident import (
    checkmk_incident_is_inactive as _checkmk_incident_is_inactive,
    extract_host,
    host_matches,
    max_severity,
)


def test_checkmk_incident_closes_when_all_members_are_inactive():
    members = [
        ("cmk:docker0177.example.com:NiFi_Flow.SAP", "checkmk"),
        ("cmk:docker0177.example.com:NiFi_Flow.SAP.Sabris", "checkmk"),
    ]

    assert _checkmk_incident_is_inactive(members, set()) is True


def test_checkmk_incident_stays_open_while_one_member_is_active():
    members = [
        ("cmk:docker0177.example.com:NiFi_Flow.SAP", "checkmk"),
        ("cmk:docker0177.example.com:NiFi_Flow.SAP.Sabris", "checkmk"),
    ]

    assert _checkmk_incident_is_inactive(members, {members[1][0]}) is False


def test_checkmk_poll_does_not_close_mixed_source_incident():
    members = [
        ("cmk:docker0177.example.com:NiFi_Flow.SAP", "checkmk"),
        ("graylog:docker0177:error", "graylog"),
    ]

    assert _checkmk_incident_is_inactive(members, set()) is False


def test_host_matches_fqdn_and_short_name():
    assert host_matches("mu-img-docker19.example.com", ["mu-img-docker19"])
    assert host_matches("cueservices", ["cueservices.cue.example.com"])
    assert not host_matches("cuedb.cue.example.com", ["cueservices.cue.example.com"])
    assert not host_matches("x.example.com", [])


def test_extract_host_only_accepts_fqdns():
    assert extract_host({"metadata": {"host": "cuedb.cue.example.com"}}) == "cuedb.cue.example.com"
    assert extract_host({"metadata": {"host": "2c51e08bb9c9"}}) == ""


def test_max_severity():
    assert max_severity({"high", "critical"}) == "critical"
    assert max_severity({"low"}) == "low"
