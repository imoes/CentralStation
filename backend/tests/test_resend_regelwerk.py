"""resend_pending holt keine Meldungen nach, die dem Regelwerk gehören — sonst Mail ohne Incident-Link nach jedem
Neustart (gemessen 07.–09.11.2026: 22 Mails, gebündelt nach den Backend-Neustarts)."""
import pathlib
import re

from app.services.error_clusters.notify import INCIDENT_ART, nicht_nachholen


def test_uebernommene_art_wird_nicht_nachgeholt():
    assert nicht_nachholen("log", {"log_cluster"})
    assert not nicht_nachholen("log", {"fleet"})
    assert nicht_nachholen("checkmk_fleet", {"fleet"}) and nicht_nachholen("critical", {"critical"})
    assert not nicht_nachholen("unbekannt", {"log_cluster", "fleet", "critical"})


def test_artnamen_sind_dieselben_wie_beim_eroeffnen():
    # Identität: runner/fleet/critical fragen regeln_uebernehmen mit genau diesen Namen
    quellen = {"log_cluster": "runner.py", "fleet": "fleet.py", "critical": "critical.py"}
    for art, datei in quellen.items():
        text = pathlib.Path(f"app/services/error_clusters/{datei}").read_text()
        assert re.search(rf'regeln_uebernehmen\("{art}"\)', text), (art, datei)
    assert set(INCIDENT_ART.values()) == set(quellen)
