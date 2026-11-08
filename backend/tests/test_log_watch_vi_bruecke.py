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
    text, tags, quelle, w = vb.erkenntnis_aus_bekannt(e)
    assert text.startswith("Bekannter Fehler (Relevanz 3, 12 Wiederholungen") and "ORA-12541" in text
    assert "thema:datenbank" in tags and "host:cue0149.example.com" in tags and "herkunft:log-ueberwachung" in tags
    assert "signal:dringend" not in tags and "befund:x" not in tags          # eigene Statistik/Verknüpfungen bleiben hier
    assert quelle.startswith("CentralStation Befund ki:28612e59e8") and w == 3
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
    assert "[Selbst]" not in vb.abschnitt({"hinweise": ["[Selbst] Ich bin die VI …", "echt"]}, "", None)   # vi-mcp ≥ 0.4.0
    assert "nicht erreichbar: HTTP 503" in vb.abschnitt(None, "", "HTTP 503")
    assert "nichts bekannt" in vb.abschnitt({"hinweise": []}, "nichts im Gedächtnis zu „x“", None)
