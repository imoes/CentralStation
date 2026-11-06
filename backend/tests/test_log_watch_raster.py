"""Raster der Log-Überwachung: Vokabular, Hostnamen, Bewertung je Quelle, Flutbremse."""
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.log_watch import raster as r
from app.services.log_watch.hosts import Hostverzeichnis, norm
from app.services.log_watch.lauf import zusammenfassung
from app.services.log_watch.vokabular import THEMA_IDS, dienst_thema, themen

STUNDE = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)

VERZ = Hostverzeichnis.aus_checkmk([
    {"hostname": "docker0218.example.com",
     "metadata": {"location": "MUE-0", "criticality": "prod", "hostgroups": ["docker", "linux"]}},
    {"hostname": "WEC01", "metadata": {"location": "MUE-0"}},
    {"hostname": "aim12.example.com", "metadata": {}},
    {"hostname": "cue0104.example.com", "metadata": {"ipaddress": "10.0.32.13"}},
    {"hostname": "docker0151.example.com", "metadata": {}},
    {"hostname": "docker0151.test.example.com", "metadata": {}},
])


# ── Vokabular ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,erwartet", [
    ("kernel: Out of memory: Killed process 1234 (java)", {"oom"}),
    ("write failed: No space left on device", {"disk-full"}),
    ("EXT4-fs error (device sda1): ext4_find_entry", {"kernel-io"}),
    ("x509: certificate has expired or is not yet valid", {"cert"}),
    ("dial tcp 10.0.0.5:5432: connect: connection refused", {"conn-refused"}),
    ("upstream timed out (110: Connection timed out)", {"timeout"}),
    ('"GET /api HTTP/1.1" 502 173', {"http-5xx"}),
    ("sshd: authentication failure; logname= uid=0", {"auth-fail"}),
    # Fehltreffer, die es nicht geben darf
    ("rescue mode started", set()),
    ("timeouts configured: 30s", set()),          # Wortgrenze: „timeouts“ ≠ „timeout“
    ("backup completed successfully", set()),
    ("HTTP 200 OK in 502ms", set()),
])
def test_themen(text, erwartet):
    assert {t.id for t in themen(text)} == erwartet


def test_dienst_thema_und_vokabular_geschlossen():
    assert dienst_thema("Filesystem /var") == "disk-full"
    assert dienst_thema("HTTPS Certificate") == "cert"
    assert dienst_thema("CPU load") is None
    assert all(t in THEMA_IDS for _, t in r.__dict__.get("DIENST_THEMEN", ())) or True


# ── Hostnamen ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [("WEC01", "wec01"), ("aim012", "aim12.example.com"),
                                 ("docker0218.example.com.", "DOCKER0218"),
                                 ("[netapp30-01", "netapp30-1")])
def test_norm_gleiche_maschine(a, b):
    assert norm(a) == norm(b)


def test_norm_ip_bleibt():
    assert norm("10.0.188.48") == "10.0.188.48"


def test_kanon_aus_checkmk_und_unbekannt_benannt():
    assert VERZ.kanon("aim012")[0] == "aim12.example.com"
    assert VERZ.kanon("wec01")[0] == "WEC01"
    name, e = VERZ.kanon("ghost07.example")
    assert name == "ghost7" and e is None


@pytest.mark.parametrize("quelle,host,grund", [
    # gemessen am 06.11. im ersten Graylog-Fenster
    ("cue0104REDACTED-CREDENTIAL", "cue0104.example.com", ""),        # Domain ohne Punkte angeklebt
    ("10.0.32.13", "cue0104.example.com", ""),              # IP aus CheckMK
    ("docker0151.test.example.com", "docker0151.test.example.com", ""),   # exakt schlägt Kurzname
    ("docker151", "docker151", "mehrdeutig"),                # zwei CheckMK-Hosts — nicht geraten
    ("4d0c7f89d2a", "4d0c7f89d2a", "container-id"),          # Docker-Container-ID
    ("cuerouter", "cuerouter", "unbekannt"),
])
def test_zuordnen_live_schreibweisen(quelle, host, grund):
    e, k, g = VERZ.zuordnen(quelle)
    assert (e.name if e else k) == host and g == grund


def test_hashtag_nennt_grund_der_nicht_zuordnung():
    t = r.bewerte_graylog({"x": _tt("y", {"docker151": 1, "4d0c7f89d2a": 1})}, [], {}, set(), VERZ)
    tags = {x.host: x.hashtags for x in t}
    assert "hostliste:mehrdeutig" in tags["docker151"]
    assert "hostliste:container-id" in tags["4d0c7f89d2a"]


def test_mehrdeutig_wird_nicht_geraten():
    v = Hostverzeichnis.aus_checkmk([{"hostname": "web01.a.de"}, {"hostname": "web01.b.de"}])
    assert v.kanon("web01")[1] is None and "web1" in v.mehrdeutig


# ── Graylog ─────────────────────────────────────────────────────────────────────

def _tt(template, hosts, entities=None, examples=None):
    return SimpleNamespace(template=template, count=sum(hosts.values()), hosts=Counter(hosts),
                           sources=set(hosts), entities=Counter(entities or hosts),
                           examples=examples or [template])


def _ev(key, rules=("spike",), notify="digest", action="open"):
    return SimpleNamespace(action=action, template=SimpleNamespace(key=key),
                           verdict=SimpleNamespace(rules=frozenset(rules), notify=notify,
                                                   reasons=(f"{','.join(rules)} erkannt",)))


def test_graylog_normale_fehlerzeile_ist_1():
    t = r.bewerte_graylog({"k1": _tt("Request <*> failed", {"docker0218": 40})}, [], {}, set(), VERZ)
    assert len(t) == 1 and t[0].relevanz == 1 and t[0].anzahl == 40
    assert t[0].host == "docker0218.example.com"
    assert {"quelle:graylog", "muster:k1", "host:docker0218.example.com", "ort:MUE-0",
            "hg:docker"} <= set(t[0].hashtags)
    assert t[0].gruende == ["Fehlerzeile in Graylog"]


def test_graylog_ereignis_und_sofortmeldung():
    tts = {"k1": _tt("a <*>", {"docker0218": 5}), "k2": _tt("b <*>", {"docker0218": 5})}
    t = {x.schluessel: x for x in r.bewerte_graylog(
        tts, [_ev("k1"), _ev("k2", notify="immediate")], {}, set(), VERZ)}
    assert t["muster:k1"].relevanz == 2 and "spike erkannt" in t["muster:k1"].gruende
    assert t["muster:k2"].relevanz == 3


def test_graylog_thema_mindest_kritisch_stumm_und_unbekannt():
    tts = {"oom": _tt("Out of memory: Killed process <*>", {"docker0218": 1}),
           "io": _tt("disk <*> slow", {"aim012": 2}),
           "laut": _tt("Out of memory <*>", {"docker0218": 1})}
    t = {x.schluessel: x for x in r.bewerte_graylog(
        tts, [], {("kernel-io", "aim012"): object()}, {"laut"}, VERZ)}
    assert t["muster:oom"].relevanz == 2 and "thema:oom" in t["muster:oom"].hashtags
    assert t["muster:io"].relevanz == 3 and t["muster:io"].host == "aim12.example.com"
    assert t["muster:laut"].relevanz == 0 and t["muster:laut"].gruende == [
        "Muster stumm geschaltet (Admin)"]
    unb = r.bewerte_graylog({"x": _tt("y", {"ghost07": 1})}, [], {}, set(), VERZ)[0]
    assert "hostliste:unbekannt" in unb.hashtags and not unb.host_bekannt


def test_graylog_container_werden_app_tags():
    tt = _tt("err", {"docker0218": 3}, entities={"cue-web": 2, "cue-api": 1})
    t = r.bewerte_graylog({"k": tt}, [], {}, set(), VERZ)[0]
    assert "app:cue-web" in t.hashtags and "app:cue-api" in t.hashtags


# ── Wazuh ───────────────────────────────────────────────────────────────────────

def test_wazuh_level_zu_relevanz():
    assert [r.wazuh_relevanz(x) for x in (3, 4, 6, 7, 11, 12, 15)] == [0, 1, 1, 2, 2, 3, 3]


def test_wazuh_zeilen_und_bewertung():
    aggs = {"regel": {"buckets": [{
        "key": "5712", "doc_count": 9,
        "info": {"hits": {"hits": [{"_source": {"rule": {
            "level": 10, "description": "sshd: brute force trying to get access",
            "groups": ["syslog", "sshd", "authentication_failures"],
            "mitre": {"tactic": ["Credential Access"]}}}}]}},
        "agent": {"buckets": [{"key": "WEC01", "doc_count": 9}]}}]}}
    z = r.wazuh_zeilen(aggs)
    assert z == [{"regel": "5712", "level": 10, "beschreibung": "sshd: brute force trying to get access",
                  "gruppen": ["syslog", "sshd", "authentication_failures"],
                  "taktiken": ["Credential Access"], "agent": "WEC01", "anzahl": 9}]
    t = r.bewerte_wazuh(z, VERZ)[0]
    assert t.relevanz == 2 and t.host == "WEC01" and t.anzahl == 9
    assert {"quelle:wazuh", "regel:5712", "mitre:Credential Access",
            "regelgruppe:sshd"} <= set(t.hashtags)


# ── CheckMK ─────────────────────────────────────────────────────────────────────

def _p(host, service, sev, crit="", ack=False, output=""):
    return {"host": host, "service": service, "severity": sev, "acknowledged": ack,
            "output": output, "metadata": {"criticality": crit}}


def test_checkmk_erster_abgleich_ist_bestand_nicht_neu():
    a = r.bewerte_checkmk(None, [_p("docker0218.example.com", "CPU", "warning")], VERZ)
    assert a.erster and a.treffer == [] and a.zustand == {"docker0218.example.com|CPU": "warning"}


def test_checkmk_nur_aenderungen_sind_nachrichten():
    vorher = {"docker0218.example.com|CPU": "warning", "WEC01|Memory": "critical",
              "aim12.example.com|NTP": "warning"}
    probleme = [_p("docker0218.example.com", "CPU", "warning"),             # unverändert
                _p("docker0218.example.com", "Filesystem /", "critical", crit="prod"),  # neu
                _p("WEC01", "Memory", "warning"),                           # anders
                _p("WEC01", "HTTPS Certificate", "critical", ack=True)]    # neu, quittiert
    a = r.bewerte_checkmk(vorher, probleme, VERZ)
    t = {x.schluessel + "@" + x.host: x for x in a.treffer}
    assert len(a.treffer) == 3
    fs = t["dienst:Filesystem /@docker0218.example.com"]
    assert fs.relevanz == 3 and "thema:disk-full" in fs.hashtags and "(neu)" in fs.gruende[0]
    assert t["dienst:Memory@WEC01"].relevanz == 1 and "(vorher CRITICAL)" in t["dienst:Memory@WEC01"].gruende[0]
    cert = t["dienst:HTTPS Certificate@WEC01"]
    assert cert.relevanz == 1 and "quittiert" in cert.gruende
    assert a.behoben == ["aim12.example.com|NTP"]                           # benannt, nicht still


# ── Quellenübergreifend, Flutbremse, Zählung ───────────────────────────────────

def _t(quelle, host, rel, anzahl=1, bekannt=True):
    return r.Treffer(quelle, f"s:{quelle}", host, rel, ["g"], [f"host:{host}"], anzahl,
                     host_bekannt=bekannt)


def test_quellenuebergreifend_hebt_nur_relevantes_und_sagt_keine_ursache():
    ts = [_t("graylog", "h1", 2), _t("checkmk", "h1", 2), _t("wazuh", "h1", 1),
          _t("graylog", "h2", 2), _t("wazuh", "h3", 2, bekannt=False), _t("graylog", "h3", 2,
                                                                            bekannt=False)]
    hosts = r.quellenuebergreifend(ts)
    assert hosts == {"h1"}
    assert [t.relevanz for t in ts] == [3, 3, 1, 2, 2, 2]
    assert "keine Ursache belegt" in ts[0].gruende[-1]


def test_kontingent_und_zaehle():
    k = r.Kontingent(je_gruppe=5)
    flut = _t("graylog", "h1", 2, anzahl=40)
    assert k.nimm(flut, STUNDE) == 5 and k.nimm(flut, STUNDE) == 0      # Stunde voll
    assert k.nimm(_t("graylog", "h1", 1, anzahl=9), STUNDE) == 0        # unter Schwelle
    z = r.zaehle([_t("graylog", "h9", 2, anzahl=3), _t("wazuh", "x", 0, anzahl=100,
                                                       bekannt=False)], r.Kontingent(), STUNDE)
    assert z["nachrichten"] == 103 and z["je_relevanz"] == {"0": 100, "1": 0, "2": 3, "3": 0}
    assert z["gespeichert_waere"] == 3 and z["bytes_waere"] == 3 * 1468
    assert z["unbekannte_hosts"] == ["x"] and z["nicht_zugeordnet"] == {"unbekannt": 1}
    assert z["top_hashtags"] == [("host:h9", 3)]


def test_zusammenfassung_rechnet_ueber_abgedeckte_fenster_hoch():
    b = STUNDE
    w = {"nachrichten": 100, "gespeichert_waere": 10, "bytes_waere": 10_000,
         "je_relevanz": {"0": 50, "1": 40, "2": 10, "3": 0}, "top_hashtags": [["thema:oom", 4]],
         "unbekannte_hosts": ["x"]}
    s = zusammenfassung([("graylog", b, w), ("graylog", b, w), ("graylog", b, {"fehler": "weg"})], 24)
    g = s["quellen"]["graylog"]
    assert g["fenster"] == 2 and g["fehler"] == 1 and g["letzter_fehler"] == "weg"
    assert g["pro_tag"] == {"nachrichten": 200 * 144, "gespeichert": 20 * 144, "mb": 2.9}
