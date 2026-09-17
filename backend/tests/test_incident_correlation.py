from app.services.incident.correlator import _checkmk_incident_is_inactive


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
