"""Raster der Log-Überwachung: Vokabular, Hostnamen, Bewertung je Quelle, Flutbremse."""
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.log_watch import raster as r
from app.services.log_watch.definition import KRITERIEN_V1
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
    from app.services.log_watch.vokabular import DIENST_THEMEN
    assert all(t in THEMA_IDS for _, t in DIENST_THEMEN)


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
    tt = SimpleNamespace(template="y", count=2, hosts=Counter({"docker151": 1, "4d0c7f89d2a": 1}),
                         sources={"docker151", "4d0c7f89d2a"}, entities=Counter(), examples=["y"], belege={})
    t = r.bewerte_graylog({"x": tt}, [], {}, set(), VERZ)
    tags = {x.host: x.hashtags for x in t}
    assert "hostliste:mehrdeutig" in tags["docker151"]
    assert "hostliste:container-id" in tags["4d0c7f89d2a"]


def test_mehrdeutig_wird_nicht_geraten():
    v = Hostverzeichnis.aus_checkmk([{"hostname": "web01.a.de"}, {"hostname": "web01.b.de"}])
    assert v.kanon("web01")[1] is None and "web1" in v.mehrdeutig


# ── Definition: Vorrang ─────────────────────────────────────────────────────────

# Vorrang: bewiesen im Regelwerk-Test (alle Kombinationen gegen das Prüf-Orakel)


def test_definition_vollstaendig_und_eindeutig():
    ids = [k.id for k in KRITERIEN_V1]
    assert len(ids) == len(set(ids))
    assert {k.art for k in KRITERIEN_V1} == {"relevant", "nicht_relevant"}
    assert {"R1", "R8", "N0", "N6"} <= set(ids)


# ── Graylog ─────────────────────────────────────────────────────────────────────

def _tt(template, hosts, entities=None, examples=None, belege=None):
    return SimpleNamespace(template=template, count=sum(hosts.values()), hosts=Counter(hosts),
                           sources=set(hosts), entities=Counter(entities or hosts),
                           examples=examples or [template], belege=belege or {})


def _ev(key, rules=("spike",), notify="digest", action="open"):
    return SimpleNamespace(action=action, template=SimpleNamespace(key=key),
                           verdict=SimpleNamespace(rules=frozenset(rules), notify=notify,
                                                   reasons=(f"{','.join(rules)} erkannt",)))


def test_graylog_bekanntes_muster_ist_routine():
    t = r.bewerte_graylog({"k1": _tt("Request <*> failed", {"docker0218": 40},
                                     belege={"docker0218": [("id1", "t")]})}, [], {}, set(), VERZ)
    assert len(t) == 1 and (t[0].urteil, t[0].kriterien, t[0].relevanz) == ("nicht_relevant", ["N1"], 1)
    assert t[0].anzahl == 40 and t[0].host == "docker0218.example.com" and t[0].belege == [("id1", "t")]
    assert {"quelle:graylog", "muster:k1", "host:docker0218.example.com", "ort:MUE-0",
            "hg:docker", "kritikalitaet:prod"} <= set(t[0].hashtags)


def test_graylog_regeln_werden_kriterien():
    tts = {k: _tt(f"{k} <*>", {"docker0218": 5}) for k in ("neu", "lang", "breit", "spitze")}
    evs = [_ev("neu", ("new",)), _ev("lang", ("persistent",)), _ev("breit", ("spread",)),
           _ev("spitze", ("spike",), notify="immediate")]
    t = {x.schluessel: x for x in r.bewerte_graylog(tts, evs, {}, set(), VERZ)}
    assert t["muster:neu"].kriterien == ["R2"] and t["muster:lang"].kriterien == ["R2"]
    assert t["muster:breit"].kriterien == ["R3"] and t["muster:breit"].relevanz == 2
    assert t["muster:spitze"].kriterien == ["R4"] and t["muster:spitze"].relevanz == 3
    assert "spike erkannt" in t["muster:spitze"].gruende


def test_graylog_schaden_kernel_stumm_und_nicht_produktion():
    verz = Hostverzeichnis.aus_checkmk([{"hostname": "devbox01", "metadata": {"criticality": "dev"}},
                                        {"hostname": "aim12.example.com", "metadata": {}}])
    tts = {"oom": _tt("Out of memory: Killed process <*>", {"aim12": 1}),
           "io": _tt("disk <*> slow", {"aim012": 2}),
           "laut": _tt("Out of memory <*>", {"aim12": 1}),
           "dev": _tt("request <*> failed", {"devbox01": 1}),
           "devoom": _tt("Out of memory: Killed <*>", {"devbox01": 1})}
    t = {x.schluessel: x for x in r.bewerte_graylog(
        tts, [_ev("dev", ("new",))], {("kernel-io", "aim012"): object()}, {"laut"}, verz)}
    assert (t["muster:oom"].urteil, t["muster:oom"].kriterien) == ("relevant", ["R5"])
    assert t["muster:io"].kriterien == ["R5"] and t["muster:io"].relevanz == 3   # Kernel-Signatur
    assert (t["muster:laut"].kriterien, t["muster:laut"].relevanz) == (["N6", "N1"], 0)   # N6 entscheidet
    assert (t["muster:dev"].urteil, t["muster:dev"].kriterien) == ("nicht_relevant", ["N3", "N1"])
    assert t["muster:dev"].verdraengt == ["R2", "N1"]          # R2 passte, N3 entschied vorher
    assert t["muster:devoom"].urteil == "relevant"                                 # R5 schlägt N3


def test_graylog_unbekannter_host_benannt_und_container_als_app():
    t = r.bewerte_graylog({"x": _tt("y", {"ghost07": 1}, entities={"cue-web": 1})}, [], {}, set(), VERZ)[0]
    assert "hostliste:unbekannt" in t.hashtags and not t.host_bekannt and "app:cue-web" in t.hashtags


# ── Wazuh ───────────────────────────────────────────────────────────────────────

def _wz(regel="5712", level=10, gruppen=("syslog", "sshd"), agent="WEC01", beschreibung="sshd: brute force"):
    return {"regel": regel, "level": level, "beschreibung": beschreibung, "gruppen": list(gruppen),
            "taktiken": ["Credential Access"], "agent": agent, "anzahl": 9}


def test_wazuh_zeilen():
    aggs = {"regel": {"buckets": [{
        "key": "5712", "doc_count": 9,
        "info": {"hits": {"hits": [{"_source": {"rule": {
            "level": 10, "description": "sshd: brute force", "groups": ["syslog", "sshd"],
            "mitre": {"tactic": ["Credential Access"]}}}}]}},
        "agent": {"buckets": [{"key": "WEC01", "doc_count": 9}]}}]}}
    assert r.wazuh_zeilen(aggs) == [_wz()]


def test_wazuh_kriterien():
    t = {x.schluessel + str(x.relevanz): x for x in r.bewerte_wazuh([
        _wz(), _wz("9999", 13), _wz("5501", 3), _wz("31151", 10, ("web", "web_scan"))], VERZ)}
    assert (t["regel:57122"].urteil, t["regel:57122"].kriterien) == ("relevant", ["R6"])
    assert t["regel:99993"].kriterien == ["R6"]                       # ab Level 12 dringend
    assert t["regel:55010"].kriterien == ["N4"]                       # PAM-Rauschen
    assert t["regel:311510"].kriterien == ["N4"]                      # Webscanner: Rauschen trotz Level 10
    assert {"quelle:wazuh", "regel:5712", "mitre:Credential Access",
            "regelgruppe:sshd"} <= set(t["regel:57122"].hashtags)


# ── CheckMK ─────────────────────────────────────────────────────────────────────

def _p(host, service, sev, crit="", ack=False, downtime=False, output=""):
    return {"host": host, "service": service, "severity": sev, "acknowledged": ack,
            "in_downtime": downtime, "output": output, "metadata": {"criticality": crit}}


def test_checkmk_erster_abgleich_ist_bestand_nicht_neu():
    a = r.bewerte_checkmk(None, [_p("docker0218.example.com", "CPU", "warning")], VERZ)
    assert a.erster and a.treffer == [] and a.zustand == {"docker0218.example.com|CPU": "warning"}


def test_checkmk_kriterien_nur_fuer_aenderungen():
    h = "docker0218.example.com"
    vorher = {f"{h}|CPU": "warning", "WEC01|Memory": "critical", "aim12.example.com|NTP": "warning"}
    probleme = [_p(h, "CPU", "warning", "prod"),                          # unverändert
                _p(h, "Filesystem /", "critical", "prod"),               # R1 + R5, dringend
                _p(h, "HTTP", "critical", "staging"),                    # R1, staging, nicht dringend
                _p("WEC01", "Memory", "warning", "prod"),                # anders, aber WARN → N0
                _p("WEC01", "HTTPS Certificate", "critical", "prod", ack=True),   # N2 schlägt R
                _p("WEC01", "Disk", "critical", "test"),                 # N3
                _p("WEC01", "Check_MK Discovery", "warning", "prod"),    # N5
                _p("WEC01", "Backup", "warning", "prod", downtime=True)] # R8, aber Downtime → N2
    a = r.bewerte_checkmk(vorher, probleme, VERZ)
    t = {x.schluessel: x for x in a.treffer}
    assert len(a.treffer) == 7
    assert (t["dienst:Filesystem /"].kriterien, t["dienst:Filesystem /"].relevanz) == (["R5", "R1"], 3)
    assert t["dienst:Filesystem /"].regel == "R5" and "signal:schaden" in t["dienst:Filesystem /"].hashtags
    assert (t["dienst:HTTP"].kriterien, t["dienst:HTTP"].relevanz) == (["R1"], 2)
    assert t["dienst:Memory"].kriterien == ["N0"] and "(vorher CRITICAL)" in t["dienst:Memory"].gruende[0]
    assert t["dienst:HTTPS Certificate"].kriterien == ["N2"] and "quittiert" in t["dienst:HTTPS Certificate"].gruende
    assert t["dienst:Disk"].kriterien == ["N3"]
    assert (t["dienst:Check_MK Discovery"].kriterien, t["dienst:Check_MK Discovery"].relevanz) == (["N5"], 0)
    assert t["dienst:Backup"].kriterien == ["N2"] and "in Downtime" in t["dienst:Backup"].gruende
    assert a.behoben == ["aim12.example.com|NTP"]


# ── Quellenübergreifend, Flutbremse, Zählung ───────────────────────────────────

def _t(quelle, host, urteil="relevant", anzahl=1, bekannt=True, kriterien=("R2",)):
    rel = 2 if urteil == "relevant" else 1
    return r.Treffer(quelle, f"s:{quelle}", host, rel, ["g"], [f"host:{host}"], anzahl,
                     host_bekannt=bekannt, urteil=urteil, kriterien=list(kriterien))


def test_quellenuebergreifend_r7_nur_fuer_relevantes_ohne_ursache():
    ts = [_t("graylog", "h1"), _t("checkmk", "h1"), _t("wazuh", "h1", "nicht_relevant", kriterien=("N0",)),
          _t("graylog", "h2"), _t("wazuh", "h3", bekannt=False), _t("graylog", "h3", bekannt=False)]
    assert r.quellenuebergreifend(ts) == {"h1"}
    assert [t.relevanz for t in ts] == [3, 3, 1, 2, 2, 2]
    assert ts[0].kriterien == ["R2", "R7"] and ts[2].kriterien == ["N0"]
    assert "keine Ursache belegt" in ts[0].gruende[-1]


def test_kontingent_und_zaehle():
    k = r.Kontingent(je_gruppe=5)
    flut = _t("graylog", "h1", anzahl=40)
    assert k.nimm(flut, STUNDE) == 5 and k.nimm(flut, STUNDE) == 0      # Stunde voll
    assert k.nimm(_t("graylog", "h1", "nicht_relevant", anzahl=9), STUNDE) == 0
    z = r.zaehle([_t("graylog", "h9", anzahl=3),
                  _t("wazuh", "x", "nicht_relevant", anzahl=100, bekannt=False, kriterien=("N0",))],
                 r.Kontingent(), STUNDE)
    assert z["nachrichten"] == 103 and z["je_urteil"] == {"relevant": 3, "nicht_relevant": 100}
    assert z["je_kriterium"] == {"N0": 100, "R2": 3}
    assert z["je_regel"] == {"default": 103} and z["regel_verdraengt"] == {} == z["regel_test"]
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


def test_neue_definition_wird_geprueft():
    from app.services.log_watch.definition import PARAMETER_V1, als_daten, pruefe
    kriterien, parameter = als_daten()
    assert pruefe(kriterien, parameter) == []
    assert "Kennungen fehlen: R7" in pruefe([k for k in kriterien if k["id"] != "R7"], parameter)
    assert any("art muss" in f for f in pruefe(kriterien[:-1] + [{**kriterien[-1], "art": "egal"}], parameter))
    assert pruefe(kriterien, {**PARAMETER_V1, "erfunden": 1}) == ["unbekannte Parameter: erfunden"]


def test_checkmk_zustand_ueberlebt_neustart_nur_wenn_jung():
    from datetime import timedelta
    from app.services.log_watch.lauf import gueltiger_zustand
    w = {"zustand": {"h|CPU": "warning"}}
    assert gueltiger_zustand(w, timedelta(minutes=7)) == {"h|CPU": "warning"}
    assert gueltiger_zustand(w, timedelta(hours=3)) is None            # zu alt: erster Abgleich
    assert gueltiger_zustand({"fehler": "x"}, timedelta(minutes=1)) is None
    assert gueltiger_zustand(None, timedelta(0)) is None
