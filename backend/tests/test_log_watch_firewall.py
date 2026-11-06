"""Firewall im Raster: Gruppen, betroffener Host, Urteil nach Regelwerk, Zählung; Definition v2."""
from app.services.log_watch import firewall as fw
from app.services.log_watch.definition import CODE_VERSION, STANDARD, uebernahme
from app.services.log_watch.hosts import Hostverzeichnis

VERZ = Hostverzeichnis.aus_checkmk([
    {"hostname": "dora.example.com", "metadata": {"criticality": "prod", "location": "MUE-0"}},
    {"hostname": "dns09.example.com", "metadata": {"criticality": "prod", "ipaddress": "91.239.20.8"}},
])

#: Felder wie im Live-Fund vom 06.11. (Sinkhole dora → mx.adobe-ai.ru; Spyware-Drop am DNS)
SINKHOLE = {"_id": "a1", "source": "nfw062.example.com", "pan_log_subtype": "spyware", "action": "sinkhole",
            "threat_id": "generic:adobe-ai.ru(744129742)", "threat_severity": "medium",
            "threat_direction": "client-to-server", "source_ip": "10.0.249.6",
            "source_domain": "dora.example.com.", "destination_ip": "8.8.8.8", "source_zone": "ADMIN_DMZ",
            "destination_zone": "UNTRUST", "destination_geo_country": "United States",
            "rule_name": "ADMIN_DMZ.UNTRUST_DMZ_INFRA_SVC", "misc": "mx.adobe-ai.ru", "timestamp": "t1"}
DROP_LOW = {"_id": "b1", "source": "nfw062.example.com", "pan_log_subtype": "spyware", "action": "drop",
            "threat_id": "Proxy:mask.icloud.com(109010004)", "threat_severity": "low",
            "threat_direction": "client-to-server", "source_ip": "91.239.20.8", "destination_ip": "9.9.9.9",
            "source_zone": "SERVER_DMZ", "destination_zone": "UNTRUST", "misc": "mask.icloud.com"}
ALERT_MEDIUM = {**DROP_LOW, "_id": "c1", "action": "alert", "threat_severity": "medium",
                "threat_id": "X:y(1)", "threat_direction": "server-to-client",
                "destination_domain": "dora.example.com."}
#: Live 06.11.: Angriff von außen — UNTRUST → WEB_DMZ; betroffen ist das Ziel, nicht der Angreifer
RCE = {"_id": "d1", "source": "nfw062.example.com", "pan_log_subtype": "vulnerability",
       "action": "reset-both", "threat_id": "React Server Components RCE(96787)", "threat_severity": "critical",
       "threat_direction": "client-to-server", "source_ip": "93.152.224.190", "source_zone": "UNTRUST",
       "destination_ip": "91.239.20.8", "destination_zone": "WEB_DMZ"}
EXT = {"UNTRUST"}


def test_gruppen_je_bedrohung_host_aktion_mit_belegen():
    g = fw.gruppen([SINKHOLE, {**SINKHOLE, "_id": "a2"}, DROP_LOW], EXT)
    assert len(g) == 2
    s = next(x for x in g if x["aktion"] == "sinkhole")
    assert s["anzahl"] == 2 and s["belege"] == [("a1", "t1"), ("a2", "t1")] and s["host"] == "dora.example.com."


def test_betroffen_ist_die_interne_seite():
    assert fw._betroffen(SINKHOLE, EXT)[:1] == ("dora.example.com.",)   # intern fragt nach außen
    assert fw._betroffen(RCE, EXT) == ("91.239.20.8", "91.239.20.8", "93.152.224.190")   # Angriff von außen
    t = fw.bewerte_firewall(fw.gruppen([RCE], EXT), VERZ, STANDARD)[0]
    assert t.host == "dns09.example.com" and "gegenstelle:93.152.224.190" in t.hashtags
    assert (t.urteil, t.relevanz) == ("relevant", 3)


def test_urteile_nach_regelwerk():
    t = {next(h for h in x.hashtags if h.startswith("aktion:")): x
         for x in fw.bewerte_firewall(fw.gruppen([SINKHOLE, DROP_LOW, ALERT_MEDIUM], EXT), VERZ, STANDARD)}
    sink, drop, alert = t["aktion:sinkhole"], t["aktion:drop"], t["aktion:alert"]
    assert (sink.urteil, sink.regel, sink.kriterien, sink.relevanz) == ("relevant", "R6-FW", ["R6"], 3)
    assert sink.host == "dora.example.com" and "ort:MUE-0" in sink.hashtags
    assert "Sinkhole" in sink.gruende[-1] and sink.belege == [("a1", "t1")]
    assert (drop.urteil, drop.kriterien) == ("nicht_relevant", ["N0"])
    assert drop.host == "dns09.example.com"                            # über die Adresse
    assert drop.gruende[-1] == "von der Firewall abgewehrt, niedrige Stufe"
    assert (alert.urteil, alert.relevanz) == ("relevant", 2)          # medium, nicht dringend
    assert {"quelle:firewall", "firewall:nfw062", "zielland:United States"} <= set(sink.hashtags)


def test_zaehlung_aus_pivot():
    rows = [{"key": ["end", "allow"], "source": "leaf", "values": [{"value": 2066504}]},
            {"key": ["drop", "drop"], "source": "leaf", "values": [{"value": 256964}]},
            {"key": ["end"], "source": "non-leaf", "values": [{"value": 1}]}]
    z = fw.zaehlung_aus_pivot(rows, ["pan_log_subtype", "action"])
    assert z == [{"pan_log_subtype": "end", "action": "allow", "anzahl": 2066504},
                 {"pan_log_subtype": "drop", "action": "drop", "anzahl": 256964}]
    assert fw.zusammen(z) == {"allow": 2066504, "drop": 256964}


def test_definition_uebernahme_erschoepfend():
    assert uebernahme(None, None) == "leer"
    assert uebernahme(1, "system") == "system_aelter"                  # Code-Standard wird übernommen
    assert uebernahme(1, "admin@x") == "admin_aelter"                  # Admin-Version bleibt, benannt
    assert uebernahme(CODE_VERSION, "system") == "aktuell"
    assert uebernahme(CODE_VERSION + 3, "admin@x") == "aktuell"


def test_bedrohungsabfrage_trifft_keinen_verkehr():
    q = fw.BEDROHUNG_QUERY
    assert q.startswith("pan_log_subtype:(") and "gim_event_type" not in q
    for verkehr in ("end", "drop", "deny", "start", "url"):
        assert f'"{verkehr}"' not in q
