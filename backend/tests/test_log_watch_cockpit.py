"""KI-Cockpit: Aggregation der lw_*-Widgets und ihre Aufnahme in den generativen Designer."""
from datetime import datetime, timedelta, timezone

from app.services.dashboard.generative_designer import (
    _ALLOWED_CONFIG_KEYS, _DEFAULT_SIZE, _ensure_log_watch_widgets, _validate_widgets,
)
from app.services.log_watch import cockpit as c
from app.services.log_watch.definition import als_daten

T0 = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)
KRIT, _ = als_daten()


def _w(kriterium=None, urteil=None, n=0, gesp=0, tags=None, **extra):
    return {"nachrichten": n, "je_kriterium": kriterium or {}, "je_urteil": urteil if urteil is not None else {},
            "gespeichert_waere": gesp, "top_hashtags": tags or [], **extra}


def test_kriterien_summiert_je_quelle_r_vor_n_mit_titel():
    z = [("wazuh", T0, _w({"R6": 141, "N4": 3014, "N0": 3544})),
         ("graylog", T0, _w({"N1": 54000, "R5": 2})),
         ("wazuh", T0 + timedelta(minutes=5), _w({"R6": 9}))]
    d = c.kriterien(z, KRIT, 24)
    assert [k["id"] for k in d["kriterien"]] == ["R5", "R6", "N0", "N1", "N4"]
    r6 = d["kriterien"][1]
    assert r6 == {"id": "R6", "titel": "Sicherheit", "art": "relevant", "anzahl": 150,
                  "je_quelle": {"wazuh": 150}}
    assert d["einheit"] == "Nachrichten" and d["zeitraum_h"] == 24


def test_volumen_je_stunde_mit_abdeckung_und_ausfall():
    z = [("graylog", T0, _w(n=100, urteil={"relevant": 2}, gesp=2)),
         ("graylog", T0 + timedelta(minutes=5), _w(n=50)),
         ("graylog", T0 + timedelta(hours=1), {"fehler": "Timeout"}),
         ("uebergreifend", T0, {"n_hosts": 0})]
    d = c.volumen(z, 2)
    g = d["quellen"]["graylog"]
    assert g["reihe"] == [{"stunde": T0.isoformat(), "gelesen": 150, "relevant": 2, "gespeichert": 2}]
    assert (g["fenster"], g["fenster_erwartet"], g["fehler"]) == (2, 24, 1)
    assert d["quellen"]["checkmk"]["fenster"] == 0                     # nie gelaufen: 0, nicht fehlend


def test_hashtags_trend_neu_ist_benannt_und_quelle_fehlt():
    seit = T0 + timedelta(hours=1)
    z = [("wazuh", T0, _w(tags=[["thema:oom", 2], ["quelle:wazuh", 9]])),
         ("wazuh", seit, _w(tags=[["thema:oom", 6], ["mitre:Impact", 3], ["quelle:wazuh", 9]]))]
    d = c.hashtags(z, seit)
    assert d["hashtags"] == [
        {"tag": "thema:oom", "anzahl": 6, "vorher": 2, "faktor": 3.0, "vergleich": "faktor"},
        {"tag": "mitre:Impact", "anzahl": 3, "vorher": 0, "faktor": None, "vergleich": "neu"}]


def test_hashtags_ohne_vergleichszeitraum_sind_nicht_neu():
    """Gemessen 06.11.: nach einem Fassungswechsel stand jeder Hashtag auf „neu“."""
    seit = T0 + timedelta(hours=1)
    alt = {"nachrichten": 5, "top_hashtags": [["mitre:Impact", 9]]}          # alte Fassung
    z = [("wazuh", T0, alt), ("wazuh", seit, _w(tags=[["mitre:Impact", 3]]))]
    assert c.hashtags(z, seit)["hashtags"][0]["vergleich"] == "kein_vergleich"


def test_korpus_laeufe_ok_veraltet_fehler_nie():
    jetzt = T0 + timedelta(minutes=40)
    d = c.korpus([("graylog", "aktiv", 488), ("graylog", "ohne_werte", 703)],
                 [("graylog", T0, {"nicht_zugeordnet": {"unbekannt": 22, "container-id": 7}})],
                 {"graylog": (T0 + timedelta(minutes=35), {}),
                  "wazuh": (T0, {}),
                  "checkmk": (T0 + timedelta(minutes=35), {"fehler": "HTTP 500"})},
                 1, False, jetzt)
    assert d["felder"] == {"graylog": {"aktiv": 488, "ohne_werte": 703}}
    assert d["nicht_zugeordnet"] == {"unbekannt": 22, "container-id": 7}
    assert [d["laeufe"][q]["zustand"] for q in ("graylog", "wazuh", "checkmk")] == ["ok", "veraltet", "fehler"]
    assert d["laeufe"]["checkmk"]["fehler"] == "HTTP 500"
    assert c.korpus([], [], {}, 1, False, jetzt)["laeufe"]["wazuh"] == {"zustand": "nie gelaufen"}


def test_designer_kennt_alle_lw_typen_mit_groesse_und_typlaenge():
    for t in c.TYPEN:
        assert t in _ALLOWED_CONFIG_KEYS and t in _DEFAULT_SIZE and len(t) <= 20   # widget_type String(20)


def test_designer_klemmt_konfiguration_der_lw_typen():
    w = _validate_widgets([{"type": "lw_hashtags", "title": "Trends", "config": {"hours": 9999, "limit": 0}},
                           {"type": "lw_korpus", "title": "Korpus", "config": {"hours": 5}}],
                          {"open_incidents": [], "forecast_candidates": [], "vitals": []})
    cfg = {x["widget_type"]: x["config"] for x in w}
    assert cfg["lw_hashtags"] == {"hours": 168, "limit": 10}            # 0 ist sinnlos → Standard
    assert cfg["lw_korpus"] == {}                                        # fremde Schlüssel fallen weg


def test_cockpit_zeigt_immer_das_warum_und_bei_ausfall_den_korpus():
    s = {"log_watch": {"relevant_1h": 0, "quellen_mit_fehler": ["checkmk"]}}
    specs = _ensure_log_watch_widgets([{"widget_type": "stat", "config": {}}], s, "de")
    assert [x["widget_type"] for x in specs] == ["lw_korpus", "lw_kriterien", "stat"]
    already = [{"widget_type": "lw_kriterien", "config": {}}]
    assert _ensure_log_watch_widgets(already, {"log_watch": {"quellen_mit_fehler": []}}, "de") == already
    assert _ensure_log_watch_widgets([], {"log_watch": {}}, "de") == []      # ohne Log-Überwachung: nichts


def test_alte_fassung_wird_ausgewiesen_nicht_gemischt():
    alt = {"nachrichten": 900, "gespeichert_waere": 400, "top_hashtags": [["mitre:Impact", 500]]}
    neu = _w(n=100, urteil={"relevant": 10}, gesp=10, tags=[["mitre:Impact", 10]])
    z = [("wazuh", T0, alt), ("wazuh", T0 + timedelta(minutes=5), neu)]
    v = c.volumen(z, 1)["quellen"]["wazuh"]
    assert v["summe"] == {"gelesen": 100, "relevant": 10, "gespeichert": 10} and v["alte_fassung"] == 1
    h = c.hashtags(z, T0)["hashtags"]
    assert h == [{"tag": "mitre:Impact", "anzahl": 10, "vorher": 0, "faktor": None,
                  "vergleich": "kein_vergleich"}]                  # davor nur alte Fassung
    # Konsistenz: kein Hashtag zählt mehr relevante Nachrichten, als es relevante gibt
    assert all(x["anzahl"] <= v["summe"]["relevant"] for x in h)


def test_befunde_als_objekte_und_benannter_leerstand():
    from app.services.log_watch.cockpit import befunde
    hit = {"_id": "ki:abc", "_source": {"zeit": "2026-11-06T21:00:00Z", "host": "veeam-proxy01", "relevanz": 3,
           "ai_befund": "dm-8 meldet Lesefehler.", "hashtags": ["thema:kernel-io", "quelle:ki"],
           "kriterien": ["R5"], "ai_provenance": {"titel": "Plattenfehler", "hypothesen": [
               {"text": "Platte defekt", "status": "offen"}], "belege": [{"id": "graylog:m1", "zitat": "Buffer I/O"}],
               "verworfen": [{"id": "x", "grund": "y"}],
               "schritte": [{"werkzeug": "lw_suche", "argumente": {"filter": "host:veeam-proxy01"}, "gesamt": 4}]}}}
    d = befunde([hit], {"gesamt": 0, "hosts": []}, True, 24)
    b = d["befunde"][0]
    assert d["state"] == "ok" and b["titel"] == "Plattenfehler" and b["verworfen"] == 1
    assert b["schritte"] == [{"werkzeug": "lw_suche", "filter": "host:veeam-proxy01", "gesamt": 4}]
    leer = befunde([], {"gesamt": 12, "hosts": [{"host": "a", "anzahl": 12}]}, False, 24)
    assert leer["state"] == "empty" and "12 Nachrichten" in leer["grund"] and "ausgeschaltet" in leer["grund"]
    assert "nicht wahr" in befunde([], {"gesamt": 0}, True, 24)["grund"]


def test_befunde_stehen_vorne_wenn_es_welche_gibt():
    s = {"log_watch": {"quellen_mit_fehler": [], "befunde_24h": [{"titel": "x", "host": "a", "relevanz": 3}]}}
    specs = _ensure_log_watch_widgets([{"widget_type": "stat", "config": {}}], s, "de")
    assert [x["widget_type"] for x in specs] == ["lw_befunde", "lw_kriterien", "stat"]
    ohne = _ensure_log_watch_widgets([], {"log_watch": {"quellen_mit_fehler": []}}, "de")
    assert "lw_befunde" not in [x["widget_type"] for x in ohne]
