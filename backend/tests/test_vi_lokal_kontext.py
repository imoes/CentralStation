"""Log-Analyse und Incident-Triage fragen die LOKALE VI, sonst das Firmennetz „firma“ (Nutzer 13.11.2026:
„Lokale VI + Firmennetz“). Erfahrungen bleiben zentral (vorher/nachher)."""
import asyncio
import json

from app.services.error_clusters import triage
from app.services.log_watch import vi_bruecke as vb

NETZ = ("Live search in network firma: asked 1 member(s), 1 answered in 306 ms · 3 hit(s), unreviewed — data\n"
        "  [aaa111] erkenntnis from vi-69f4 (1 server(s))\n"
        "     CentralStation-Befund: Solr p99 8521 ms CRITICAL auf cue0124\n"
        "     content: …\n"
        "  [bbb222] erkenntnis from vi-69f4 (1 server(s))\n"
        "     Confluence IT: [KB] cue0124.example.com – Solr neu starten\n"
        "  [ccc333] erkenntnis from vi-69f4 (1 server(s))\n"
        "     Bekannter Fehler (Relevanz 3, 0 Wiederholungen ohne KI): Solr p999\n")


def test_netz_anfrage_aus_hosts_und_themen_ohne_musterhash():
    assert vb.netz_anfrage(["host:cue0124.example.com", "thema:solr", "muster:6afe289cdc715add"]) == \
        "cue0124.example.com solr"


def test_eigene_centralstation_eintraege_werden_entfernt():
    """Keine Selbstbestätigung: zentral galt !instanz:centralstation, im Firmennetz steht keine Instanz."""
    text, weg = vb.ohne_eigene(NETZ)
    assert weg == 2 and "Confluence IT" in text and "CentralStation-Befund" not in text
    assert "Bekannter Fehler" not in text and text.startswith("Live search")


class _K:
    def __init__(self, lokal, netz):
        self.lokal, self.netz, self.aufrufe = lokal, netz, []

    async def werkzeug(self, name, arg, timeout=20.0):
        self.aufrufe.append((name, arg))
        return self.lokal if name == "vi_recall" else self.netz


def _mit(monkeypatch, k, vi={"url": "http://vi-mcp:8765/mcp", "token": "t"}):
    from app.services import vi_konsole
    from app.services.connectors import vi_mcp

    async def lade(db):
        return vi
    monkeypatch.setattr(vi_konsole, "lade", lade)
    monkeypatch.setattr(vi_mcp, "ViMcpConnector", lambda url, cred: k)

    class _Sess:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False
    import sys
    import types
    # app.core.database lädt ohne .env nicht — eine Attrappe genügt, die Sitzung wird nicht benutzt
    monkeypatch.setitem(sys.modules, "app.core.database", types.SimpleNamespace(AsyncSessionLocal=lambda: _Sess()))


def test_lokal_zuerst_dann_firmennetz(monkeypatch):
    k = _K("2 objects for …\n  [x] lokal", NETZ)
    _mit(monkeypatch, k)
    text, info = asyncio.run(vb.lokaler_kontext(["host:cue0124.example.com"]))
    assert info["quelle"] == "lokal" and [a[0] for a in k.aufrufe] == ["vi_recall"]
    assert k.aufrufe[0][1]["reinforce"] is False                         # nur lesend
    k = _K("nothing in memory for “host:cue0124”", NETZ)
    _mit(monkeypatch, k)
    text, info = asyncio.run(vb.lokaler_kontext(["host:cue0124.example.com"], zusatz="Solr <*> timeout"))
    assert info["quelle"] == "firma" and info["treffer"] == 1 and info["eigene_entfernt"] == 2
    assert k.aufrufe[1] == ("vi_net_search", {"network": "firma", "query": "cue0124.example.com Solr timeout"})


def test_ohne_lokale_vi_benannt(monkeypatch):
    _mit(monkeypatch, _K("", ""), vi=None)
    assert asyncio.run(vb.lokaler_kontext(["host:x"]))[1]["quelle"] == "aus"


def test_triage_prompt_traegt_das_gedaechtnis_mit_hinweis():
    p = json.loads(triage.build_prompt({"template": "t", "hosts": ["h"]}, [], [],
                                       {"quelle": "firma", "text": "  [bbb] Confluence IT: [KB] h"}))
    assert p["gedaechtnis"]["quelle"] == "firma" and "kein Beleg" in p["gedaechtnis"]["hinweis"]
    assert "gedaechtnis" not in json.loads(triage.build_prompt({"template": "t"}, [], [], {"quelle": "nichts", "text": ""}))
    assert "gedaechtnis" in triage.SYSTEM
