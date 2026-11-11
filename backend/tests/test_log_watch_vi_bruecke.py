"""Brücke zum Kollektiv-Gedächtnis vi-mcp (Konnektor vi_mcp, vi_bruecke.py) — reine Teile."""
from types import SimpleNamespace

from app.services.connectors import vi_mcp
from app.services.log_watch import vi_bruecke as vb


def test_konnektor_liest_json_und_sse_und_text():
    assert vi_mcp.lies_body('{"jsonrpc":"2.0","id":1,"result":{"a":1}}')["result"] == {"a": 1}
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"ok"}]}}\n\n'
    assert vi_mcp.antwort_text(vi_mcp.lies_body(sse)["result"]) == "ok"
    try:
        vi_mcp.lies_body("<html>")
        assert False
    except vi_mcp.ViMcpFehler as e:
        assert "weder JSON noch SSE" in str(e)
    # VALID_TYPES ohne Import (app.api.connectors zieht die DB-Modelle nach — im Testcontainer ohne Base)
    import pathlib
    assert '"vi_mcp"' in pathlib.Path("app/api/connectors.py").read_text()
    assert '"vi_mcp": ViMcpConnector' in pathlib.Path("app/services/connectors/__init__.py").read_text()


def test_bekannter_fehler_wird_erkenntnis_mit_quelle():
    e = {"titel": "ORA-12541 auf cue0149", "status": "aktiv", "befund": "ki:28612e59e8", "relevanz": 3,
         "wiederholungen": 12, "filter": "muster:abc host:cue0149.example.com", "fehler": "muster:abc",
         "hashtags": ["muster:abc", "thema:datenbank", "signal:dringend", "befund:x", "kritikalitaet:prod"],
         "hosts": ["cue0149.example.com", "cue0111.example.com"]}
    text, tags, quelle, w, key = vb.erkenntnis_aus_bekannt(e)
    # vi-mcp 0.23.0 (Peer 10.11.): key = genau der Teil hinter „— Filter:“ → gleiche Signatur aktualisiert statt neu
    assert key == "muster:abc host:cue0149.example.com" and text.endswith(f" — Filter: {key}")
    assert vb.erkenntnis_aus_bekannt({**e, "filter": None})[4] == "muster:abc"
    assert text.startswith("Bekannter Fehler (Relevanz 3, 12 Wiederholungen") and "ORA-12541" in text
    assert "thema:datenbank" in tags and "host:cue0149.example.com" in tags and "herkunft:log-ueberwachung" in tags
    assert "signal:dringend" not in tags and "befund:x" not in tags          # eigene Statistik/Verknüpfungen bleiben hier
    assert quelle.startswith("CentralStation Befund ki:28612e59e8") and w == 3
    # Hashtags mit Leerzeichen/Klammern (CentralStation) → vi-mcp-Form, sonst lehnt vi-mcp das ganze Objekt ab (08.11.: 4 von 50)
    assert vb.vi_tag("dienst:MSSQL Blocked Sessions") == "dienst:MSSQL-Blocked-Sessions"
    assert vb.vi_tag("dienst:CPU utilization FPC: EX4300-24P 0") == "dienst:CPU-utilization-FPC:-EX4300-24P-0"
    assert vb.vi_tag("Host:x") == "host:x" and vb.vi_tag("dienst:") is None and vb.vi_tag("Ä:x") is None
    _, tags2, _, _, _ = vb.erkenntnis_aus_bekannt({**e, "hashtags": ["dienst:OMD kas performance"]})
    assert "dienst:OMD-kas-performance" in tags2 and all(" " not in t for t in tags2)
    assert vb.erkenntnis_aus_bekannt({**e, "status": "widerlegt"}) is None
    assert vb.erkenntnis_aus_bekannt({**e, "befund": None}) is None


def test_feldbedeutung_wird_erkenntnis_nur_von_ki_oder_mensch():
    fb = SimpleNamespace(name="quellfeld:checkmk.state_type", bedeutung="0 = soft, 1 = hard", herkunft="mensch",
                         quelle="Livestatus GET columns 08.11.")
    text, tags, quelle, _ = vb.erkenntnis_aus_feld(fb)
    assert text == "Bedeutung von quellfeld:checkmk.state_type: 0 = soft, 1 = hard"
    assert tags == ["feld:checkmk.state_type", "feldart:quellfeld", "herkunft:log-ueberwachung"]
    assert "Livestatus" in quelle
    assert vb.erkenntnis_aus_feld(SimpleNamespace(name="x", bedeutung="y", herkunft="system", quelle="")) is None


def test_reize_filter_und_abschnitt():
    fall = {"start": "muster:7c74c2c5c67f9cf3", "hosts": ["cue0111.example.com"]}
    lage = {"facetten": {"thema": [{"wert": "timeout", "anzahl": 3}]}}
    reize = vb.reize_aus_fall(fall, lage)
    assert reize == ["host:cue0111.example.com", "muster:7c74c2c5c67f9cf3", "thema:timeout"]
    assert vb.filter_andere(reize) == ("(host:cue0111.example.com OR muster:7c74c2c5c67f9cf3 OR thema:timeout) "
                                       "!instanz:centralstation !art:antwort !art:erfahrung")
    a = vb.abschnitt({"hinweise": ["Gelernt: cue0111 trägt CUE Print"]}, "2 Objekte zu …:\n  [erkenntnis:1] x", None)
    assert "Kollektiv-Gedächtnis" in a and "cue0111 trägt CUE Print" in a and "[erkenntnis:1] x" in a
    a2 = vb.abschnitt({"hinweise": ["[Selbst] Ich bin die VI …", "Im Langzeitlager passen: 3 Objekte", "echt"]}, "", None)
    assert "[Selbst]" not in a2 and "Langzeitlager" not in a2 and "echt" in a2      # vi-mcp ≥ 0.4.0 / 0.10.0
    assert "nicht erreichbar: HTTP 503" in vb.abschnitt(None, "", "HTTP 503")
    assert "nichts bekannt" in vb.abschnitt({"hinweise": []}, "nichts im Gedächtnis zu „x“", None)


def test_antworten_deutsch_und_englisch():
    """vi-mcp 0.23.0 antwortet englisch (Peer 10.11.2026); Ablehnungen kommen als Text („rejected: …“), nicht als
    Ausnahme — vorher zählte die Brücke sie als geschrieben. Beide Sprachen gelten."""
    assert vb.abgelehnt("rejected: hashtags missing") and vb.abgelehnt("abgelehnt: Quelle fehlt")
    assert not vb.abgelehnt("learned erkenntnis:abc") and not vb.abgelehnt("")
    assert vb.kein_fallwissen("[Self] I am the VI") and vb.kein_fallwissen("[Selbst] Ich bin die VI")
    assert vb.kein_fallwissen("Im Langzeitlager passen: 3") and vb.kein_fallwissen("Matching in the long-term archive: 2")
    from app.services.connectors.vi_mcp import antwort_text
    a = antwort_text({"content": [{"type": "text", "text": "Hashtags fehlen"}], "isError": True})
    assert vb.abgelehnt(a) and a == "Hashtags fehlen"                       # isError zählt, egal welche Sprache
    assert not vb.abgelehnt(antwort_text({"content": [{"type": "text", "text": "learned"}], "isError": False}))
    assert not vb.kein_fallwissen("Learned [erkenntnis:x] (from claude-code): ORA-12541 auf cue0149")
    t = vb.abschnitt({"hinweise": ["[Self] I am the VI", "Learned: ORA-12541 auf cue0149"]},
                     "nothing in memory for “host:x”", None)
    assert "ORA-12541" in t and "[Self]" not in t and "nothing in memory" not in t


# ── vi_feedback (vi-mcp ≥ 0.49.0; Nutzer 11.11.2026: „feedback soll gesendet werden“) ──
_H1 = "Learned [erkenntnis:e490e73f326cffcc81c52775] (from claude-code; skill:docker): Docker auf hal …"
_H2 = "Rule [regel:ce8055307c5b69751c8449f1] (from vi; kollektiv:nein): Bash scheiterte 3× …"
_NACHFRAGE = "Did one of these help or mislead you? vi_feedback(helped=[ids], wrong=[ids]) — one call, no reason needed"


def test_abschnitt_bittet_um_rueckmeldung_nur_bei_gezeigten_ids():
    a = vb.abschnitt({"hinweise": [_H1, _H2, _NACHFRAGE]}, "", None)
    assert "erkenntnis:e490e73f326cffcc81c52775" in a and "regel:ce8055307c5b69751c8449f1" in a
    assert "vi_feedback(" not in a                       # die Analyse-KI hat das Werkzeug nicht
    assert vb.RUECKMELDUNG_ZEILE in a and "nie pauschal alle" in a
    # nur Treffer aus vi_erinnere (bucht vi-mcp nicht als gezeigt) → keine Bitte
    b = vb.abschnitt({"hinweise": ["Gelernt ohne ID"]}, "1 object:\n  [erkenntnis:aaaaaaaaaaaa] x", None)
    assert vb.RUECKMELDUNG_ZEILE not in b
    assert vb.RUECKMELDUNG_ZEILE not in vb.abschnitt({"hinweise": []}, "", None)


def test_gezeigte_ids_nur_aus_zugriff_und_im_prompt():
    v = {"hinweise": [_H1], "zugriff": {"gezeigt": ["erkenntnis:e490e73f326cffcc81c52775",
                                                    "erkenntnis:ffffffffffffffffffffffff"]}}
    text = vb.abschnitt(v, "", None)
    # die zweite ID bucht vi-mcp als gezeigt, sie steht aber nicht im Prompt (Zeile gekürzt/gefiltert)
    assert vb.gezeigte_ids(v, text) == ["erkenntnis:e490e73f326cffcc81c52775"]
    assert vb.gezeigte_ids("text", text) == [] and vb.gezeigte_ids({"hinweise": []}, text) == []


def test_auswahl_verwirft_ungezeigte_und_widerspruechliche_ids():
    g = ["erkenntnis:a1a1a1a1a1", "regel:b2b2b2b2b2", "erkenntnis:c3c3c3c3c3"]
    half, falsch, verw = vb.auswahl(g, ["[erkenntnis:a1a1a1a1a1]", "erkenntnis:a1a1a1a1a1", "erkenntnis:999999999"],
                                    ["regel:b2b2b2b2b2", "erkenntnis:c3c3c3c3c3", "erkenntnis:c3c3c3c3c3"])
    assert half == ["erkenntnis:a1a1a1a1a1"] and falsch == ["regel:b2b2b2b2b2", "erkenntnis:c3c3c3c3c3"]
    assert verw == {"erkenntnis:999999999": "nicht als Hinweis dieser Analyse gezeigt"}
    h2, f2, v2 = vb.auswahl(g, ["regel:b2b2b2b2b2"], ["regel:b2b2b2b2b2"])
    assert h2 == f2 == [] and v2 == {"regel:b2b2b2b2b2": "in vi_half und vi_falsch zugleich"}
    assert vb.auswahl(g, None, "kein Array") == ([], [], {})


class _FakeVi:
    def __init__(self, fehler=None):
        self.rufe, self.fehler = [], fehler

    async def nachher(self, *a, **k):
        self.rufe.append(("nachher", k.get("vorher_id")))

    async def rueckmeldung(self, half, falsch, vorher_id=None):
        self.rufe.append(("rueckmeldung", half, falsch, vorher_id))
        if self.fehler:
            raise self.fehler
        return vi_mcp.ViAntwort("feedback recorded for 2 hint(s) — thanks")


def _mit_fake(monkeypatch, k):
    import contextlib
    import sys

    import app.services.connectors.vi_mcp as vm
    dbm = sys.modules["app.core.database"]          # Stub aus conftest.py

    @contextlib.asynccontextmanager
    async def _sitzung():
        yield None

    async def _lade(_db):
        return k
    monkeypatch.setattr(dbm, "AsyncSessionLocal", _sitzung, raising=False)
    monkeypatch.setattr(vm, "load_vi_mcp_connector", _lade)


def test_rueckmeldung_nach_nachher_nur_mit_benutzten_ids(monkeypatch):
    import asyncio
    k = _FakeVi()
    _mit_fake(monkeypatch, k)
    fall = {"start": "host:x", "hosts": ["x"]}
    erg = {"befund": "ki:1", "titel": "t", "relevanz": 2,
           "vi_half": ["erkenntnis:a1a1a1a1a1"], "vi_falsch": ["regel:b2b2b2b2b2", "erkenntnis:nie0gezeigt0"]}
    r = asyncio.run(vb.nach_analyse(fall, erg, 1200, "v1", ["erkenntnis:a1a1a1a1a1", "regel:b2b2b2b2b2", "erkenntnis:c3c3c3c3c3"]))
    # Reihenfolge: erst die Erfahrung (vi_after), sonst bucht vi_feedback nichts (intuition.feedback sucht art=erfahrung)
    # dieselbe vorher_id wie vi_nachher → before_id (vi-mcp 0.50.1: genau diese Erfahrung, nicht die eines Nachbarn)
    assert k.rufe == [("nachher", "v1"), ("rueckmeldung", ["erkenntnis:a1a1a1a1a1"], ["regel:b2b2b2b2b2"], "v1")]
    assert r["gezeigt"] == 3 and r["antwort"].startswith("feedback recorded")
    assert r["verworfen"] == {"erkenntnis:nie0gezeigt0": "nicht als Hinweis dieser Analyse gezeigt"}
    # gezeigt, aber nichts benutzt → nichts senden (nie pauschal alle als „half“), benannt
    k2 = _FakeVi()
    _mit_fake(monkeypatch, k2)
    r2 = asyncio.run(vb.nach_analyse(fall, {"befund": "ki:2"}, 1, "v2", ["erkenntnis:a1a1a1a1a1"]))
    assert k2.rufe == [("nachher", "v2")] and r2["gesendet"].startswith("nein")
    # nichts gezeigt → kein Eintrag; Ausfall der Rückmeldung → benannt, kein Abbruch
    assert asyncio.run(vb.nach_analyse(fall, {"befund": "ki:3"}, 1, "v3", [])) is None
    k3 = _FakeVi(fehler=vi_mcp.ViMcpFehler("HTTP 503"))
    _mit_fake(monkeypatch, k3)
    r3 = asyncio.run(vb.nach_analyse(fall, {**erg}, 1, "v4", ["erkenntnis:a1a1a1a1a1"]))
    assert r3["fehler"].startswith("ViMcpFehler: HTTP 503")


def test_lw_befund_nimmt_rueckmeldung_an_und_schreibt_sie_nicht_ins_dokument():
    import inspect

    from app.services.log_watch import werkzeuge as wz
    sig = inspect.signature(wz.lw_befund)
    assert "vi_half" in sig.parameters and "vi_falsch" in sig.parameters
    schema = wz.REGISTRY["lw_befund"][2]
    assert "vi_half" in schema["properties"] and "vi_half" not in schema["required"]
    import pathlib
    src = pathlib.Path("app/services/log_watch/werkzeuge.py").read_text()
    aufruf = src[src.index("await schreibe_befund("):src.index("except BefundAbgelehnt")]
    assert "vi_half" not in aufruf and "vi_falsch" not in aufruf


def test_konnektor_schickt_before_id_nur_wenn_bekannt(monkeypatch):
    import asyncio
    k = vi_mcp.ViMcpConnector("http://x/mcp", {"token": "t"})
    gesendet = []

    async def _werkzeug(name, arg, timeout=20.0):
        gesendet.append((name, arg))
        return vi_mcp.ViAntwort("ok")
    monkeypatch.setattr(k, "werkzeug", _werkzeug)
    asyncio.run(k.rueckmeldung(["erkenntnis:a"], [], "v9"))
    asyncio.run(k.rueckmeldung([], ["regel:b"]))
    assert gesendet == [("vi_feedback", {"helped": ["erkenntnis:a"], "wrong": [], "before_id": "v9"}),
                        ("vi_feedback", {"helped": [], "wrong": ["regel:b"]})]
