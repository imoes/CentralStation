"""Bedeutung jedes Felds und jeder Hashtag-Art einer Nachricht — EINE Quelle für den Skill, den die KI vor
jeder Analyse liest (Nutzer 07.11.: „wenn neue Felder dazukommen, müssen diese dokumentiert werden; die
Feldbedeutung gehört in den KI-Prompt — wir brauchen einen Skill, den die KI liest, bevor sie analysiert“).

Neue Felder bleiben nicht still undokumentiert:
  - Feld der Historie (historie.MAPPING) ohne Eintrag hier → Test schlägt fehl (test_log_watch_felder);
  - Hashtag-Art, die live vorkommt, aber hier fehlt → `undokumentiert()` nennt sie (KI-Prüfung, Cockpit).
Der Skill (`skill()`) entsteht aus diesem Register, den Kriterien der geltenden Definition und dem
Vokabular — er ist statisch je Code-Stand (Prompt-Cache) und wird nicht von Hand gepflegt.
"""
from __future__ import annotations

#: Felder einer Nachricht in cs-logs-* — (Bedeutung, änderbar)
FELDER: dict[str, tuple[str, bool]] = {
    "zeit": ("Zeitpunkt der Nachricht in der Quelle (UTC)", False),
    "quelle": ("Herkunft: graylog, wazuh, checkmk, firewall, switch; ki = Befund der KI, konsole = Konsolen-Gespräch", False),
    "host": ("betroffener Host, kanonischer Name aus dem Hostverzeichnis (CheckMK); ein Host, ein Name in allen Quellen", False),
    "host_bekannt": ("Host steht im Hostverzeichnis (sonst Hashtag hostliste:<grund>)", False),
    "schluessel": ("Gruppenschlüssel des Rasters: muster:<drain> (Graylog/Switch), regel:<id> (Wazuh), dienst:<service> "
                   "(CheckMK), bedrohung:<name> (Firewall) — derselbe Fehler, derselbe Schlüssel", False),
    "text": ("Text der Nachricht (Zitate für Belege müssen wörtlich hieraus stammen)", False),
    "roh": ("alle Originalfelder der Quelle (z. B. roh.rule.description bei Wazuh), durchsuchbar", False),
    "level": ("Schwere in der Quelle als Zahl (Graylog-Syslog-Level, Wazuh rule.level)", False),
    "gruppe_anzahl": ("Nachrichten derselben Gruppe im 5-min-Fenster (gespeichert sind nur einige — Flutbremse)", False),
    "raster_v": ("Fassung des Rasters, das die Nachricht beurteilt hat", False),
    "vektor": ("Bedeutungsvektor (bge-m3) für die Suche nach Bedeutung — Technik, kein Inhalt", False),
    "relevanz": ("0 Routine, 1 normal, 2 auffällig, 3 dringend", True),
    "urteil": ("relevant | nicht_relevant — genau zwei Werte", True),
    "herkunft": ("wer geurteilt hat: regel < ki < mensch (Vorrang)", True),
    "kriterien": ("Kriterien der Definition, die das Urteil tragen (R1–R8 relevant, N0–N6 nicht relevant), "
                  "entscheidendes zuerst", True),
    "definition_v": ("Version der Relevanz-Definition, nach der geurteilt wurde", True),
    "gruende": ("Begründungen des Urteils in Worten", True),
    "hashtags": ("Merkmale als art:wert — Filter, Facetten und Netzplan-Knoten zugleich (siehe Hashtag-Arten)", True),
    "ai_relevanz": ("Relevanz laut KI-Befund, an Belegen, Wiederholungen und Gesehenem", True),
    "ai_hashtags": ("Hashtags, die die KI vergeben hat (nur Vokabular)", True),
    "ai_befund": ("Text des KI-Befunds (nur an Dokumenten quelle:ki)", True),
    "ai_provenance": ("Herkunft eines KI-Eintrags: Befund (Titel, Hypothesen, Belege, Schritte) oder an der Nachricht "
                      "bekannt/gesehen (welcher Befund, wann)", True),
    "aenderungen": ("Geschichte der Änderungen: zeit, wer, was, grund (nachricht.py)", True),
    "geaendert": ("Zeitpunkt der letzten Änderung", True),
}

#: Hashtag-Arten — (Bedeutung, Beispiel). Eine Art, ein Name, über alle Quellen.
HASHTAG_ARTEN: dict[str, tuple[str, str]] = {
    "quelle": ("Quelle der Nachricht", "quelle:wazuh"),
    "host": ("betroffener Host (kanonisch)", "host:cue0148.example.com"),
    "hostliste": ("Host nicht im Hostverzeichnis, mit Grund", "hostliste:unbekannt"),
    "kritikalitaet": ("Kritikalität des Hosts aus CheckMK (prod, staging, critical, test, dev, offline …)", "kritikalitaet:prod"),
    "ort": ("Standort des Hosts", "ort:MUE-0"),
    "netz": ("Netzzone des Hosts (intern | dmz)", "netz:intern"),
    "netzbereich": ("Netzbereich innerhalb der Zone", "netzbereich:server-dmz"),
    "label": ("CheckMK-Label des Hosts", "label:host.group=dmz"),
    "hg": ("CheckMK-Hostgruppe (an Gesprächen)", "hg:cue"),
    "signal": ("Merkmal, das das Raster gesetzt hat — siehe Signale", "signal:dringend"),
    "thema": ("Symptom aus dem festen Vokabular — siehe Themen", "thema:disk-full"),
    "muster": ("Drain-Muster einer Graylog-/Switch-Zeile (Hash; Text über den Namen)", "muster:c68edc363ede4401"),
    "regel": ("Wazuh-Regel-ID (Name = roh.rule.description)", "regel:533"),
    "regelgruppe": ("Wazuh-Regelgruppe", "regelgruppe:syscheck"),
    "decoder": ("Wazuh-Decoder, der die Zeile gelesen hat", "decoder:sshd"),
    "logquelle": ("Wazuh-Logquelle (location)", "logquelle:journald"),
    "mitre": ("MITRE-ATT&CK-Taktik", "mitre:Credential Access"),
    "mitre-technik": ("MITRE-ATT&CK-Technik-ID (Name = roh.rule.mitre.technique)", "mitre-technik:T1110"),
    "level": ("Schwere der Quelle als Hashtag", "level:3"),
    "zustand": ("CheckMK-Zustand (crit, warn, unknown)", "zustand:crit"),
    "dienst": ("CheckMK-Service", "dienst:Filesystem /"),
    "switch": ("Switch, von dem die Zeile stammt", "switch:nsa383"),
    "app": ("Anwendung/Container der Zeile", "app:cue-content-store-06"),
    "befund": ("Verknüpfung mit einem KI-Befund (Beleg, Wiederholung oder gesehen) — ein Name für die Kante", "befund:47f532e733"),
    "incident": ("Verknüpfung mit einem Incident (sein Filter trifft die Nachricht)", "incident:4ec1a4de-…"),
    "ticket": ("Bezug einer Konsolen-Sitzung zu einem Ticket", "ticket:IMIT-1234"),
    "alert": ("Bezug einer Konsolen-Sitzung zu einem Alert", "alert:glog:da8b…"),
    "gegenstelle": ("Firewall: Gegenstelle (die andere Seite der Verbindung)", "gegenstelle:8.8.8.8"),
    "bedrohung": ("Firewall: erkannte Bedrohung (Palo Alto threat)", "bedrohung:Masscan Port Scanning Tool Detection(56115)"),
    "ip": ("Firewall: betroffene interne Adresse", "ip:10.0.249.6"),
    "aktion": ("Firewall: Aktion (allow, deny, drop, reset-both, sinkhole …)", "aktion:reset-both"),
    "schwere": ("Firewall: Schwere der Bedrohung", "schwere:medium"),
    "zone": ("Firewall: Quellzone", "zone:UNTRUST"),
    "zielzone": ("Firewall: Zielzone", "zielzone:TRUST"),
    "firewall": ("Firewall, die gemeldet hat", "firewall:nfw062"),
    "zielland": ("Firewall: Land des Ziels", "zielland:Germany"),
}

#: Signale (regelwerk.SIGNALE) — was das Raster aus Quelle und Statistik ableitet
SIGNALE: dict[str, str] = {
    "neu": "Fehler-Cluster: Muster neu oder anhaltend neu (R2)",
    "anstieg": "Fehler-Cluster: deutlich über der Grundlast (R4)",
    "ausbreitung": "Fehler-Cluster: auf vielen Hosts/Containern zugleich (R3)",
    "dringend": "Sofort-Meldung der Statistik, CRIT auf dringender Kritikalität, Wazuh-Level ≥ 12, kritische Kernelmeldung — Relevanz 3",
    "schaden": "Schaden an Ressourcen: Speicher, Platte, Dateisystem, E/A, Absturz, Kernel (R5)",
    "frist": "ablaufende Frist: Zertifikat, Sicherung (R8)",
    "bedrohung": "Firewall-Bedrohung ab Stufe medium oder Sinkhole (R6)",
    "behandelt": "quittiert, in Downtime oder offenes Ticket am Incident (N2)",
    "stumm": "vom Admin stumm geschaltet (N6)",
}


def undokumentiert_aus(arten: list[str]) -> list[str]:
    """Hashtag-Arten ohne Eintrag im Register. Rein — testbar."""
    return sorted({a for a in arten if a and a not in HASHTAG_ARTEN})


async def undokumentiert(stunden: float = 24) -> list[str]:
    """Live vorkommende Hashtag-Arten, die das Register nicht kennt — benannt, nicht still."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    r = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 0, "query": {"range": {"zeit": {"gte": f"now-{int(stunden)}h"}}},
        "aggs": {"t": {"terms": {"field": "hashtags", "size": 20000}}}})
    return undokumentiert_aus([b["key"].split(":", 1)[0] for b in r["aggregations"]["t"]["buckets"]])


def skill(kriterien: list[dict] | None = None) -> str:
    """Der Skill, den die KI vor jeder Analyse liest: Felder, Hashtag-Arten, Signale, Relevanz, Kriterien,
    Themen — erzeugt aus Register, Definition und Vokabular (statisch je Code-Stand)."""
    from app.services.log_watch.definition import KRITERIEN
    from app.services.log_watch.vokabular import THEMEN
    krit = kriterien or [{"id": k.id, "art": k.art, "titel": k.titel} for k in KRITERIEN]
    z = ["SKILL: Felder und Hashtags einer Nachricht (lies das vor der Analyse)", "",
         "Felder (fest = Fakt der Quelle, nie geändert; änderbar = Urteil, nur über Methoden mit Grund):"]
    z += [f"- {n} ({'änderbar' if a else 'fest'}): {b}" for n, (b, a) in FELDER.items()]
    z += ["", "Hashtag-Arten (art:wert — zugleich Filter, Facette und Netzplan-Knoten):"]
    z += [f"- {a}: {b} — z. B. {bsp}" for a, (b, bsp) in HASHTAG_ARTEN.items()]
    z += ["", "Signale (signal:<name>):"] + [f"- {n}: {b}" for n, b in SIGNALE.items()]
    z += ["", "Relevanz: 0 Routine · 1 normal · 2 auffällig (gespeichert) · 3 dringend (gespeichert, zur KI).",
          "Kriterien (das erste entscheidet; Vorrang N6 > N2 > N4 > R5/R6 > N3/N5 > übrige R > N1 > N0):"]
    z += [f"- {k['id']} ({'relevant' if k['art'] == 'relevant' else 'nicht relevant'}): {k['titel']}" for k in krit]
    z += ["", "Themen (thema:<id>): " + "; ".join(f"{t.id} = {t.text}" for t in THEMEN)]
    return "\n".join(z)
