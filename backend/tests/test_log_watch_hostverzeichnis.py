"""lw_hostverzeichnis: das Hostverzeichnis als lesendes MCP-Werkzeug (für die AIKB, 09.11.2026)."""
import asyncio

from app.services.log_watch import hosts as h


def _verz():
    return h.Hostverzeichnis.aus_checkmk([
        {"hostname": "cue0109.example.com", "metadata": {"location": "MUE-0", "criticality": "prod",
                                                         "hostgroups": ["oracle"], "ipaddress": "10.1.2.3"}},
        {"hostname": "web01.example.com", "metadata": {"netz": "dmz", "netzbereich": "web-dmz"}},
        {"hostname": "web01.example.net", "metadata": {}},          # gleicher Kurzname → mehrdeutig
    ])


def test_liste_jeder_host_einmal_mit_adresse_und_mehrdeutigkeit():
    liste = _verz().als_liste()
    assert [x["name"] for x in liste] == ["cue0109.example.com", "web01.example.net", "web01.example.com"]
    cue = liste[0]
    assert cue == {"name": "cue0109.example.com", "ort": "MUE-0", "kritikalitaet": "prod", "hostgruppen": ["oracle"],
                   "netz": "", "netzbereich": "", "adresse": "10.1.2.3", "kurzname_mehrdeutig": False}
    assert liste[1]["kurzname_mehrdeutig"] and liste[2]["kurzname_mehrdeutig"]
    assert liste[2]["netz"] == "dmz" and liste[2]["netzbereich"] == "web-dmz"


def _werkzeug(monkeypatch, verz, name=""):
    from app.services.log_watch import werkzeuge

    class _Db:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False

    async def _v(db): return verz
    monkeypatch.setattr(h, "verzeichnis", _v)
    import importlib
    monkeypatch.setattr(importlib.import_module("app.core.database"), "AsyncSessionLocal", lambda: _Db())
    return asyncio.run(werkzeuge.lw_hostverzeichnis(name))


def test_werkzeug_liste_und_zuordnung(monkeypatch):
    v = _verz()
    r = _werkzeug(monkeypatch, v)
    assert r["anzahl"] == 3 and r["mehrdeutige_kurznamen"] == ["web1"] and "quelle" in r
    assert _werkzeug(monkeypatch, v, "CUE0109")["host"]["name"] == "cue0109.example.com"
    assert _werkzeug(monkeypatch, v, "10.1.2.3")["host"]["name"] == "cue0109.example.com"
    m = _werkzeug(monkeypatch, v, "web01")
    assert m["zugeordnet"] is False and m["grund"] == h.MEHRDEUTIG and "hostliste:mehrdeutig" in m["hashtags"]
    u = _werkzeug(monkeypatch, v, "gibtsnicht01")
    assert u["grund"] == h.UNBEKANNT


def test_leeres_verzeichnis_ist_benannt(monkeypatch):
    r = _werkzeug(monkeypatch, h.Hostverzeichnis())
    assert r["anzahl"] == 0 and r["zustand"].startswith("leer:")


def test_mcp_server_bietet_das_werkzeug():
    import pathlib
    assert "async def lw_hostverzeichnis(" in pathlib.Path("app/api/mcp_server.py").read_text()
