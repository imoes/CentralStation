"""Die Incident-Einschätzung fragt SearXNG (Nutzer 17.11.2026: „Aber das hatte ich dir gesagt!! Es soll searxng
hergenommen werden.“ — gebaut war die Websuche bis dahin nur in der Log-Analyse)."""
import asyncio
import json

from app.services.error_clusters import notify, triage
from app.services.log_watch import websuche

CUE = {"template": "host:cue0111.example.com", "hosts": ["cue0111.example.com"],
       "examples": ["cue0111 kernel: LayoutChampApp[3794845]: segfault at 51000000 ip 0000000051000000 "
                    "sp 00007f417025dc08 error 14"]}


def test_anfrage_ohne_variable_teile():
    q = triage.web_anfrage(CUE)
    assert q == "cue0111 kernel LayoutChampApp segfault at ip sp error 14"
    # Host entfernt danach die Websuche selbst (bereinige) — nichts Internes geht ins Netz
    assert websuche.bereinige(q)[0] == "kernel LayoutChampApp segfault at ip sp error 14"


def test_anfrage_aus_drain_muster_und_ohne_daten():
    assert triage.web_anfrage({"template": "nginx upstream timed out <*> while reading <*>"}) == \
        "nginx upstream timed out while reading"
    assert triage.web_anfrage({"template": "host:h1"}) == ""            # Regel-Schlüssel ist keine Meldung


def _stub(monkeypatch, treffer, seite_text="Answer: check ulimit"):
    aufrufe = []

    async def suche(q, warum):
        aufrufe.append(("suche", q))
        bq, weg = websuche.bereinige(q)
        return {"anfrage": bq, "entfernt": weg, "treffer": treffer, "suchmaschinen": "x"}

    async def seite(url):
        aufrufe.append(("seite", url))
        return {"url": url, "text": seite_text}
    monkeypatch.setattr(websuche, "suche", suche)
    monkeypatch.setattr(websuche, "seite", seite)
    return aufrufe


def test_liest_die_erste_frage_antwort_seite(monkeypatch):
    t = [{"titel": "Kernel", "url": "https://en.wikipedia.org/wiki/Kernel", "auszug": "a"},
         {"titel": "segfault error 14", "url": "https://serverfault.com/questions/1/segfault", "auszug": "b"}]
    aufrufe = _stub(monkeypatch, t)
    w = asyncio.run(triage.websuche_fuer(CUE))
    assert w["anfrage"].startswith("kernel LayoutChampApp") and len(w["treffer"]) == 2
    assert w["seite"]["url"].startswith("https://serverfault.com") and ("seite", t[1]["url"]) in aufrufe


def test_fehler_ist_benannt(monkeypatch):
    async def suche(q, warum):
        return {"anfrage": q, "treffer": [], "fehler": "Websuche ohne Treffer, weil Suchmaschinen ausgefallen sind: "
                                                       "mojeek (Suspended: access denied)"}
    monkeypatch.setattr(websuche, "suche", suche)
    w = asyncio.run(triage.websuche_fuer(CUE))
    assert "mojeek" in w["fehler"] and "treffer" not in w
    assert "keine Beispielzeile" in asyncio.run(triage.websuche_fuer({"template": "host:x"}))["fehler"]


def test_zeitueberschreitung_haelt_die_einschaetzung_nicht_auf(monkeypatch):
    async def suche(q, warum):
        await asyncio.sleep(5)
    monkeypatch.setattr(websuche, "suche", suche)
    monkeypatch.setattr(triage, "WEB_ZEIT_S", 0.05)
    assert "TimeoutError" in asyncio.run(triage.websuche_fuer(CUE))["fehler"]


def test_treffer_gehen_in_den_prompt():
    web = {"anfrage": "q", "treffer": [{"titel": "T", "url": "https://u", "auszug": "x", "suchmaschine": "s"}],
           "seite": {"url": "https://u", "text": "Lösung"}}
    p = json.loads(triage.build_prompt(CUE, [], [], None, web))
    assert p["websuche"]["treffer"] == [{"titel": "T", "url": "https://u", "auszug": "x"}]
    assert p["websuche"]["seite"]["text"] == "Lösung" and "'websuche'" in triage.SYSTEM
    assert "websuche" not in json.loads(triage.build_prompt(CUE, [], [], None, {"fehler": "x"}))


def test_mail_zeigt_suche_oder_grund():
    h = notify._websuche_html({"anfrage": "a <b>", "treffer": [{"titel": "T", "url": "https://u"}], "gelesen": "https://u"})
    assert "SearXNG" in h and "a &lt;b&gt;" in h and 'href="https://u"' in h and "(gelesen)" in h
    assert "Kein Ergebnis: keine Treffer" in notify._websuche_html({"anfrage": "a", "fehler": "keine Treffer"})
    assert notify._websuche_html(None) == ""


def test_rueckfall_ohne_produktnamen(monkeypatch):
    """17.11. gemessen: mit LayoutChampApp 0 Treffer, ohne 34."""
    assert triage.allgemeiner("kernel LayoutChampApp segfault at ip sp error 14") == "kernel segfault at ip sp error 14"
    assert triage.allgemeiner("ORA-12541 TNS no listener") == "ORA-12541 TNS no listener"    # Fehlercodes bleiben
    assert triage.allgemeiner("error 14") == "error 14"
    gesucht = []

    async def suche(q, warum):
        gesucht.append(q)
        return {"anfrage": q, "treffer": [] if "LayoutChampApp" in q else [{"titel": "T", "url": "https://x.org/a"}]}
    monkeypatch.setattr(websuche, "suche", suche)
    w = asyncio.run(triage.websuche_fuer(CUE))
    assert len(gesucht) == 2 and w["anfrage"] == ("cue0111 kernel LayoutChampApp segfault at ip sp error 14 → "
                                                  "kernel segfault at ip sp error 14")
    assert w["treffer"][0]["url"] == "https://x.org/a"


def test_nginx_zeile_ohne_datum_und_verbindungsnummern():
    q = triage.web_anfrage({"examples": ["2026/11/17 10:00:01 [error] 123#123: *456 upstream timed out (110: Connection "
                                         "timed out) while reading response header from upstream"]})
    assert q.startswith("error upstream timed out 110 Connection timed out") and "2026" not in q and "#" not in q
