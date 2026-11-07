"""Werkzeuge außerhalb der Historie im KI-Trichter: dieselben Namen wie auf dem MCP-Server,
Ausfall und Zeitüberschreitung benannt, Antwort in der Ansicht der Lage."""
import asyncio

from app.services.log_watch import hostobjekt, trichter
from app.services.log_watch import werkzeuge as wz


def test_trichter_bietet_die_drei_quellen_an():
    namen = {t["function"]["name"] for t in wz.openai_tools(trichter.TRICHTER_WERKZEUGE)}
    assert {"get_checkmk_host", "search_tickets", "search_knowledge_base"} <= namen
    assert "lw_wissen" not in namen          # Gedächtnis steht im Prompt (zwei Wege zum selben Ergebnis)
    # keine Tiefensuche (gemessen ~280 s) und kein Schreiben
    assert set(wz.REGISTRY["search_knowledge_base"][2]["properties"]) == {"query"}
    assert not any(wz.REGISTRY[n][3] for n in ("get_checkmk_host", "search_tickets", "search_knowledge_base"))


def test_quelle_meldet_fehler_als_fehler(monkeypatch):
    import app.services.checkmk_metrics as cm

    async def ohne(hostname, db=None):
        return {"hostname": hostname, "error": "Host auf keinem CheckMK-Standort gefunden"}
    monkeypatch.setattr(cm, "host_status", ohne)
    r = asyncio.run(wz.rufe("get_checkmk_host", {"hostname": "x"}))
    assert r == {"hostname": "x", "fehler": "CheckMK: Host auf keinem CheckMK-Standort gefunden"}


def test_quelle_mit_frist(monkeypatch):
    import app.services.checkmk_metrics as cm

    async def haengt(hostname, db=None):
        await asyncio.sleep(5)
    monkeypatch.setattr(cm, "host_status", haengt)
    monkeypatch.setattr(wz, "EXTERN_S", 0.05)
    r = asyncio.run(wz.rufe("get_checkmk_host", {"hostname": "x"}))
    assert r == {"fehler": "CheckMK: keine Antwort nach 0.05 s"}


def test_checkmk_antwort_zeigt_nur_was_nicht_ok_ist():
    erg = {"hostname": "cue0113", "site": "mue",
           "services": [{"name": "CPU", "state": 0, "state_label": "OK", "summary": "fine"},
                        {"name": "Filesystem /", "state": 2, "state_label": "CRIT", "summary": "97% used " * 80}],
           "performance": [{"service": "Filesystem /", "metric": "fs_used_percent", "current": 97.1,
                            "min": 96.0, "max": 97.1, "trend": "↑", "unit": "%"}]}
    k = hostobjekt.kuerze_antwort("get_checkmk_host", erg)
    assert k["ok"] == 1 and k["nicht_ok_gesamt"] == 1
    assert k["nicht_ok"][0]["service"] == "Filesystem /" and len(k["nicht_ok"][0]["ausgabe"]) == hostobjekt.TEXT_MAX
    assert k["metriken_2h"] == ["Filesystem / fs_used_percent: 97.1% (min 96.0, max 97.1, ↑)"]
    assert hostobjekt.kuerze_antwort("get_checkmk_host", {"fehler": "x"}) == {"fehler": "x"}


def test_ticket_und_wissen_kurz():
    t = hostobjekt.kuerze_antwort("search_tickets", {
        "searched": ["ServiceDesk"], "total_found": 1, "issues": [
            {"key": "IMIT-1", "summary": "Platte voll", "status": "Offen", "updated": "2026-11-07T08:00:00",
             "instance": "ServiceDesk", "match": "cue0113 /var"}]})
    assert t["tickets"] == [{"key": "IMIT-1", "titel": "Platte voll", "status": "Offen", "aktualisiert": "2026-11-07",
                             "instanz": "ServiceDesk", "stelle": "cue0113 /var"}]
    w = hostobjekt.kuerze_antwort("search_knowledge_base", {"count": 1, "results": [
        {"title": "Runbook", "url": "u", "excerpt": "x" * 5000}]})
    assert w["gesamt"] == 1 and len(w["artikel"][0]["auszug"]) == 2 * hostobjekt.TEXT_MAX


def test_tickets_ohne_globalen_konnektor_sind_ein_fehler_nicht_leer(monkeypatch):
    """Gemessen 07.11.: nur persönliche Jira-Konnektoren — „nirgends gesucht“ darf nicht wie
    „kein Ticket“ aussehen."""
    import app.services.ticketing as tk

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import app.core.database as dbmod
    monkeypatch.setattr(dbmod, "AsyncSessionLocal", lambda: _DB(), raising=False)

    async def nirgends(db, user_id, query, **kw):
        return {"ok": False, "searched": [], "count": 0, "issues": []}
    monkeypatch.setattr(tk, "search_tickets", nirgends)
    r = asyncio.run(wz.rufe("search_tickets", {"query": "x"}))
    assert r["fehler"] == f"Jira: {wz.OHNE_JIRA}"
