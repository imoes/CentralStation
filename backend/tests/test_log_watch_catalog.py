"""Feldkatalog der Log-Überwachung: Rollen-Regeln, Adapter-Parser (Graylog, Wazuh, CheckMK),
Fortschreiben."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.log_watch import catalog as c
from app.services.log_watch.quellen import checkmk as cmk
from app.services.log_watch.quellen import graylog as gl
from app.services.log_watch.quellen import wazuh as wz

JETZT = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("feld,numerisch,card,mit,rolle", [
    # Graylog
    ("container_name", False, 47, 31456, "entitaet"),
    ("source", False, 900, 600000, "entitaet"),
    ("winlog_computer_name", False, 30, 100, "entitaet"),
    ("level", True, 9, 600000, "kategorie"),
    ("http_response_code", True, 40, 5000, "kategorie"),
    ("message", False, None, 600000, "freitext"),
    ("gl2_message_id", False, 600000, 600000, "ignorieren"),
    ("container_id", False, 47, 31456, "ignorieren"),
    ("timestamp", False, 600000, 600000, "ignorieren"),
    ("http_request_time_num", True, 5000, 9000, "messwert"),
    # Wazuh (verschachtelte Namen)
    ("agent.name", False, 300, 2_000_000, "entitaet"),
    ("rule.level", True, 11, 2_000_000, "kategorie"),
    ("rule.id", False, 900, 2_000_000, "kennung"),          # Regel, nicht Einmal-ID
    ("rule.mitre.technique", False, 40, 9000, "kennung"),
    ("full_log", False, None, 2_000_000, "freitext"),
    ("id", False, 2_000_000, 2_000_000, "ignorieren"),
    ("agent.id", False, 300, 2_000_000, "ignorieren"),
    ("data.win.system.computer", False, 80, 5000, "entitaet"),
    ("data.srcip", False, 2248, 800000, "entitaet"),
    ("predecoder.timestamp", False, 89801, 1_800_000, "ignorieren"),
    # gemessene Fehlgriffe vom 06.11.: kein Hashtag aus Ports, Bytes, Zeitstempeln, Domains
    ("source_port", True, 62288, 908000, "messwert"),
    ("network_bytes", True, 54504, 670000, "messwert"),
    ("network_connection_duration", True, 2501, 670000, "messwert"),
    ("session_repeat_count", True, 49, 830000, "messwert"),
    ("timestamp3", False, 904, 830000, "ignorieren"),
    ("event_created", False, 6586, 237000, "ignorieren"),
    ("source_ip", False, 17010, 908000, "entitaet"),
    ("source_domain", False, 9322, 690000, "ignorieren"),   # > KENNUNG_MAX
    ("rule_name", False, 278, 830000, "kennung"),
    # CheckMK
    ("host_name", False, 120, 298, "entitaet"),
    ("service", False, 200, 298, "entitaet"),
    ("state", True, 3, 298, "kategorie"),
    ("plugin_output", False, 280, 298, "freitext"),
    ("check_command", False, 60, 298, "kennung"),
    ("label:cmk/os_family", False, 2, 250, "kategorie"),   # erst die Messung entscheidet
    # nur Messung
    ("url_infix", False, 12, 900, "kategorie"),
    ("command", False, 800, 9000, "kennung"),
    ("trace", False, 8000, 9000, "ignorieren"),             # fast jeder Wert einmalig
    ("selten", False, None, 0, "unbekannt"),                # im Fenster leer: benannt
])
def test_rolle(feld, numerisch, card, mit, rolle):
    assert c.bestimme_rolle(feld, numerisch, card, mit)[0] == rolle


def test_grund_ist_immer_genannt():
    for args in [("level", True, 9, 1), ("x", False, None, 0), ("y", False, 5000, 6000)]:
        assert c.bestimme_rolle(*args)[1]


# ── Graylog ─────────────────────────────────────────────────────────────────────

def test_graylog_felder():
    felder = gl.felder_aus_graylog([
        {"name": "destination_nat_port",
         "type": {"type": "compound(int,long)", "properties": ["numeric", "enumerable"]}},
        {"name": "log_file_device_id", "type": {"type": "string", "properties": ["enumerable"]}},
        {"name": "PeerNameCN", "type": {"type": "string", "properties": ["full-text-search"]}},
        {"type": {"type": "string"}},  # ohne Namen: übergangen
    ])
    assert [(f.name, f.numerisch, f.aggregierbar) for f in felder] == [
        ("destination_nat_port", True, True), ("log_file_device_id", False, True),
        ("PeerNameCN", False, False)]


def test_graylog_exists_query_maskiert():
    assert gl.exists_query("message") == "_exists_:message"
    assert gl.exists_query("label:a/b c") == r"_exists_:label\:a\/b\ c"


def test_graylog_serien_aus_pivot():
    st = {"rows": [{"key": [], "source": "leaf", "values": [
        {"key": ["count()"], "value": 672682}, {"key": ["card(container_name)"], "value": 47},
        {"key": ["count(container_name)"], "value": 31456}]}]}
    assert gl.serien_aus_pivot(st) == {"count()": 672682, "card(container_name)": 47,
                                       "count(container_name)": 31456}


def test_graylog_werte_haeufigste_zuerst():
    st = {"rows": [
        {"key": ["(Empty Value)"], "source": "leaf", "values": [{"key": ["count()"], "value": 500}]},
        {"key": ["3"], "source": "leaf", "values": [{"key": ["count()"], "value": 10}]},
        {"key": ["4"], "source": "leaf", "values": [{"key": ["count()"], "value": 99}]},
        {"key": [], "source": "non-leaf", "values": [{"key": ["count()"], "value": 109}]},
    ]}
    assert gl.werte_aus_pivot(st) == [{"wert": "4", "anzahl": 99}, {"wert": "3", "anzahl": 10}]


def test_graylog_werte_aus_stichprobe_markiert():
    w = gl.werte_aus_stichprobe(["a", "b", "a", None, "", "(Empty Value)", 3])
    assert w[0] == {"wert": "a", "anzahl": 2, "stichprobe": True}
    assert {x["wert"] for x in w} == {"a", "b", "3"}


# ── Wazuh ───────────────────────────────────────────────────────────────────────

def test_wazuh_mapping_vereinigt_indizes_und_findet_aggfeld():
    mappings = {
        "wazuh-alerts-4.x-2026.11.05": {"mappings": {"properties": {
            "agent": {"properties": {"name": {"type": "keyword"}}},
            "rule": {"properties": {"level": {"type": "long"}}},
            "full_log": {"type": "text"},
        }}},
        "wazuh-alerts-4.x-2026.11.06": {"mappings": {"properties": {
            "data": {"properties": {"title": {"type": "text",
                                              "fields": {"keyword": {"type": "keyword"}}}}},
            "GeoLocation": {"properties": {"location": {"type": "geo_point"}}},
        }}},
    }
    felder, agg = wz.felder_aus_mapping(mappings)
    assert [f.name for f in felder] == ["GeoLocation.location", "agent.name", "data.title",
                                        "full_log", "rule.level"]
    assert next(f for f in felder if f.name == "rule.level").numerisch
    assert [f.name for f in felder if not f.aggregierbar] == ["GeoLocation.location", "full_log"]
    assert agg == {"agent.name": "agent.name", "rule.level": "rule.level", "full_log": None,
                   "data.title": "data.title.keyword", "GeoLocation.location": None}


def test_wazuh_stats_aus_aggs():
    aggs = {"n0": {"doc_count": 2000}, "c0": {"value": 300},
            "n1": {"doc_count": 2000}}                      # Text: nur Abdeckung
    assert wz.stats_aus_aggs(aggs, ["agent.name", "full_log", "fehlt"]) == {
        "agent.name": {"mit_feld": 2000, "kardinalitaet": 300},
        "full_log": {"mit_feld": 2000, "kardinalitaet": None},
    }
    assert wz.werte_aus_terms({"buckets": [{"key": 3, "doc_count": 9}]}) == [
        {"wert": "3", "anzahl": 9}]


# ── CheckMK ─────────────────────────────────────────────────────────────────────

API = [
    {"extensions": {"host_name": "docker0218", "description": "Filesystem /", "state": 2,
                    "plugin_output": "95% used", "host_groups": ["docker", "linux"],
                    "scheduled_downtime_depth": 0,
                    "host_labels": {"cmk/os_family": "linux",
                                    "cmk/piggyback_source_vc011.example.com": "yes"}}},
    {"extensions": {"host_name": "vpp0221", "description": "CPU load", "state": 1,
                    "plugin_output": "load 12", "host_groups": ["proxmox"],
                    "scheduled_downtime_depth": 1,
                    "host_labels": {"cmk/os_family": "linux",
                                    "cmk/piggyback_source_vc012.example.com": "yes"}}},
]


def test_checkmk_zeilen_benennen_um_und_fassen_hostlabels_zusammen():
    z = cmk.zeilen_aus_api(API)
    assert z[0]["service"] == "Filesystem /" and z[0]["in_downtime"] is False
    assert z[1]["in_downtime"] is True
    assert z[0]["label:cmk/piggyback_source"] == "vc011.example.com"
    assert not any(k.startswith("label:cmk/piggyback_source_") for r in z for k in r)


def test_checkmk_messen_zaehlt_listen_je_element():
    z = cmk.zeilen_aus_api(API)
    felder = cmk.felder_aus_zeilen(z)
    namen = {f.name: f for f in felder}
    assert namen["state"].numerisch and namen["host_groups"].typ == "liste"
    assert "long_plugin_output" in namen                    # bekannte Spalte, hier leer
    s = cmk.messe_zeilen(z, felder)
    assert s["host_groups"] == {"mit_feld": 2, "kardinalitaet": 3,
                                "beispielwerte": [{"wert": "docker", "anzahl": 1},
                                                  {"wert": "linux", "anzahl": 1},
                                                  {"wert": "proxmox", "anzahl": 1}]}
    assert s["label:cmk/piggyback_source"]["kardinalitaet"] == 2
    assert s["long_plugin_output"] == {"mit_feld": 0, "kardinalitaet": 0}
    assert "beispielwerte" not in s["plugin_output"]        # Freitext: keine Hashtag-Werte


# ── Fortschreiben ───────────────────────────────────────────────────────────────

def _neu(feld):
    return SimpleNamespace(quelle="graylog", feld=feld, rolle=None, rolle_herkunft="regel",
                           status=None, first_seen=JETZT, gemessen_at=None, beispielwerte=None)


def _row(feld, **kw):
    r = _neu(feld)
    r.typ, r.numerisch = "string", False
    r.rolle, r.rolle_herkunft = kw.pop("rolle", "kennung"), kw.pop("herkunft", "regel")
    r.status, r.gemessen_at = kw.pop("status", "aktiv"), JETZT
    return r


def test_uebernehme_neu_aktiv_leer_verschwunden():
    bestand = {"alt": _row("alt"), "weg": _row("weg")}
    felder = [c.Feld("alt", "string", False), c.Feld("container_name", "string", False),
              c.Feld("leer", "string", False)]
    stats = {"alt": {"mit_feld": 50, "kardinalitaet": 3},
             "container_name": {"mit_feld": 100, "kardinalitaet": 40,
                                "beispielwerte": [{"wert": "cue", "anzahl": 60}]},
             "leer": {"mit_feld": 0, "kardinalitaet": 0}}
    neu, z = c.uebernehme(bestand, felder, 1000, stats, JETZT, _neu)
    assert [r.feld for r in neu] == ["container_name", "leer"]
    assert z == {"neu": 2, "aktiv": 2, "ohne_werte": 1, "verschwunden": 1, "nicht_gemessen": 0}
    assert bestand["alt"].rolle == "kategorie" and bestand["alt"].abdeckung == 0.05
    assert bestand["weg"].status == "verschwunden"          # bleibt stehen, wird benannt
    cn = next(r for r in neu if r.feld == "container_name")
    assert cn.rolle == "entitaet" and cn.beispielwerte[0]["wert"] == "cue"
    leer = next(r for r in neu if r.feld == "leer")
    assert leer.status == "ohne_werte" and leer.rolle == "unbekannt"


def test_admin_rolle_bleibt_und_nicht_gemessen_behaelt_alles():
    bestand = {"command": _row("command", rolle="entitaet", herkunft="admin"),
               "url_infix": _row("url_infix", rolle="kategorie")}
    felder = [c.Feld("command", "string", False), c.Feld("url_infix", "string", False)]
    stats = {"command": {"mit_feld": 9000, "kardinalitaet": 800}}   # url_infix: abgelehnt
    _, z = c.uebernehme(bestand, felder, 10000, stats, JETZT, _neu)
    assert bestand["command"].rolle == "entitaet"
    assert bestand["url_infix"].rolle == "kategorie"
    assert bestand["url_infix"].status == "aktiv"      # nicht gemessen ≠ ohne Werte
    assert z["nicht_gemessen"] == 1 and z["ohne_werte"] == 0


def test_neues_feld_ungemessen_ist_benannt():
    neu, _ = c.uebernehme({}, [c.Feld("rätsel", "string", False)], 100, {}, JETZT, _neu)
    assert neu[0].status == "nicht_gemessen" and neu[0].rolle == "unbekannt"
    assert neu[0].gemessen_at is None
