"""Bedeutung jedes Felds, jeder Hashtag-Art und jedes Signals einer Nachricht (Nutzer 07.11.: „wenn neue
Felder dazukommen, müssen diese dokumentiert werden“ — „die Felderklärungen werden in der Datenbank abgelegt
und per MCP-Tool nachgeschlagen; die KI muss nur neue Felder + Beschreibung selber anlegen“).

Quelle der Wahrheit ist die Tabelle `feld_bedeutung`. Dieses Modul bringt den Grundbestand (herkunft system,
einmal übernommen) und die Methoden: nachschlagen (MCP lw_feld), neu anlegen (lw_feld_anlegen — nur NEUE
Namen; Vorhandenes ändert nur ein Mensch). Nichts bleibt still undokumentiert:
  - Feld der Historie (historie.MAPPING) ohne Grundbestand → Test schlägt fehl (test_log_watch_felder);
  - live vorkommende Hashtag-Art ohne Eintrag in der Tabelle → `undokumentiert()` (Cockpit, Korpus).
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


ARTEN = ("feld", "hashtag", "signal")


def grundbestand() -> list[dict]:
    """Einträge des Code-Registers als Zeilen (name = art:bezeichnung). Rein — testbar."""
    out = [{"name": f"feld:{n}", "art": "feld", "bedeutung": b + (" (änderbar über Methoden)" if a else " (fest)"),
            "beispiel": ""} for n, (b, a) in FELDER.items()]
    out += [{"name": f"hashtag:{n}", "art": "hashtag", "bedeutung": b, "beispiel": bsp} for n, (b, bsp) in HASHTAG_ARTEN.items()]
    out += [{"name": f"signal:{n}", "art": "signal", "bedeutung": b, "beispiel": f"signal:{n}"} for n, b in SIGNALE.items()]
    return out


async def stelle_bereit(db) -> int:
    """Grundbestand einmal übernehmen — vorhandene Einträge (auch von KI oder Mensch) bleiben unberührt."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.log_watch import FeldBedeutung
    da = set((await db.execute(select(FeldBedeutung.name))).scalars())
    jetzt = datetime.now(timezone.utc)
    neu = [FeldBedeutung(**e, herkunft="system", quelle="log_watch/felder.py", angelegt_von="system",
                         angelegt_at=jetzt) for e in grundbestand() if e["name"] not in da]
    db.add_all(neu)
    await db.commit()
    return len(neu)


def name_von(art: str, bezeichnung: str) -> str:
    return bezeichnung if bezeichnung.startswith(f"{art}:") else f"{art}:{bezeichnung}"


async def nachschlagen(name: str = "") -> dict:
    """Bedeutung eines Eintrags (z. B. hashtag:regelgruppe, feld:roh, signal:neu, oder nur regelgruppe) —
    ohne Namen die ganze Liste. Unbekannt ist ein benannter Zustand mit Hinweis auf lw_feld_anlegen."""
    from sqlalchemy import or_, select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung
    async with AsyncSessionLocal() as db:
        await stelle_bereit(db)
        q = select(FeldBedeutung).order_by(FeldBedeutung.name)
        if name.strip():
            n = name.strip()
            q = q.where(or_(FeldBedeutung.name == n, FeldBedeutung.name.in_([f"{a}:{n}" for a in ARTEN])))
        rows = (await db.execute(q)).scalars().all()
    eintraege = [{"name": r.name, "art": r.art, "bedeutung": r.bedeutung, "beispiel": r.beispiel,
                  "herkunft": r.herkunft, "quelle": r.quelle} for r in rows]
    if name.strip() and not eintraege:
        return {"name": name, "unbekannt": True,
                "hinweis": "Keine Bedeutung hinterlegt — wenn du sie sicher kennst (Quelle: Nachricht, Doku oder "
                           "web_suche), lege sie mit lw_feld_anlegen an."}
    return {"eintraege": eintraege, "anzahl": len(eintraege)}


async def anlegen(name: str, art: str, bedeutung: str, beispiel: str, quelle: str, von: str) -> dict:
    """Neuen Eintrag anlegen (KI oder Mensch). Vorhandenes wird NICHT überschrieben (Vorrang: die Bedeutung
    stammt vom System oder einem Menschen) — die Ablehnung nennt den vorhandenen Eintrag."""
    from datetime import datetime, timezone

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung
    if art not in ARTEN:
        return {"abgelehnt": f"art muss {', '.join(ARTEN)} sein"}
    if len((bedeutung or "").strip()) < 10:
        return {"abgelehnt": "Bedeutung fehlt oder ist zu kurz (mindestens ein Satz)"}
    if not (quelle or "").strip():
        return {"abgelehnt": "quelle ist Pflicht: woher die Bedeutung stammt (Nachricht-ID, URL, Doku)"}
    voll = name_von(art, (name or "").strip())
    if not voll.split(":", 1)[1]:
        return {"abgelehnt": "Name fehlt"}
    async with AsyncSessionLocal() as db:
        await stelle_bereit(db)
        da = await db.get(FeldBedeutung, voll)
        if da is not None:
            return {"abgelehnt": f"{voll} ist schon beschrieben ({da.herkunft}): {da.bedeutung}"}
        db.add(FeldBedeutung(name=voll, art=art, bedeutung=bedeutung.strip()[:2000], beispiel=(beispiel or "")[:300],
                             herkunft="ki" if von == "ki" else "mensch", quelle=quelle.strip()[:2000],
                             angelegt_von=von, angelegt_at=datetime.now(timezone.utc)))
        await db.commit()
    return {"angelegt": voll}


def undokumentiert_aus(arten: list[str], bekannt: set[str]) -> list[str]:
    """Hashtag-Arten ohne Eintrag (bekannt = Namen wie hashtag:regel). Rein — testbar."""
    return sorted({a for a in arten if a and f"hashtag:{a}" not in bekannt})


async def undokumentiert(stunden: float = 24) -> list[str]:
    """Live vorkommende Hashtag-Arten, die die Tabelle nicht kennt — benannt, nicht still."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.opensearch import get_opensearch
    from app.models.log_watch import FeldBedeutung
    from app.services.log_watch.historie import PRAEFIX
    async with AsyncSessionLocal() as db:
        await stelle_bereit(db)
        bekannt = set((await db.execute(select(FeldBedeutung.name))).scalars())
    r = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 0, "query": {"range": {"zeit": {"gte": f"now-{int(stunden)}h"}}},
        "aggs": {"t": {"terms": {"field": "hashtags", "size": 20000}}}})
    return undokumentiert_aus([b["key"].split(":", 1)[0] for b in r["aggregations"]["t"]["buckets"]], bekannt)
