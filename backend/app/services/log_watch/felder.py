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

import re

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


#: quellfeld = Feld der Quelle (Graylog, Wazuh, CheckMK) aus dem Feldkatalog, Name quellfeld:<quelle>.<feld>
ARTEN = ("feld", "hashtag", "signal", "quellfeld")


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


def kandidaten(name: str) -> list[str]:
    """Namen, unter denen eine Anfrage gemeint sein kann. Rein — testbar. Ein Hashtag mit Wert
    (regelgruppe:syscheck — so fragt das Modell, gemessen 07.11.) meint die Art (hashtag:regelgruppe)."""
    n = name.strip()
    out = [n] + [f"{a}:{n}" for a in ARTEN]
    art, _, wert = n.partition(":")
    if wert and art not in ARTEN:
        out.append(f"hashtag:{art}")
        if art == "signal":
            out.append(f"signal:{wert}")
    return list(dict.fromkeys(out))


async def nachschlagen(name: str = "") -> dict:
    """Bedeutung eines Eintrags (z. B. hashtag:regelgruppe, feld:roh, signal:neu, oder nur regelgruppe) —
    ohne Namen die ganze Liste. Unbekannt ist ein benannter Zustand mit Hinweis auf lw_feld_anlegen."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung
    async with AsyncSessionLocal() as db:
        await stelle_bereit(db)
        q = select(FeldBedeutung).order_by(FeldBedeutung.name)
        if name.strip():
            q = q.where(FeldBedeutung.name.in_(kandidaten(name)))
        rows = (await db.execute(q)).scalars().all()
    eintraege = [{"name": r.name, "art": r.art, "bedeutung": r.bedeutung, "beispiel": r.beispiel,
                  "herkunft": r.herkunft, "quelle": r.quelle} for r in rows]
    if name.strip() and not eintraege:
        return {"name": name, "unbekannt": True,
                "hinweis": "Keine Bedeutung hinterlegt — wenn du sie sicher kennst (Quelle: Nachricht, Doku oder "
                           "web_suche), lege sie mit lw_feld_anlegen an."}
    return {"eintraege": eintraege, "anzahl": len(eintraege)}


async def pruefe_belege_feld(belege: list) -> tuple[list[dict], list[str]]:
    """Belege einer neuen Feldbedeutung aus der Analyse: {id, zitat} (Nachricht der Historie) oder {url, zitat}
    (mit web_seite gelesene Seite) — das Zitat muss wörtlich dort stehen (wie befund.pruefe_belege)."""
    from app.services.log_watch import websuche
    from app.services.log_watch.befund import ZITAT_MIN, _norm, lies_dokumente
    docs = await lies_dokumente([str(b.get("id")) for b in belege if isinstance(b, dict) and b.get("id")])
    ok, gruende = [], []
    for b in belege or []:
        z = str((b or {}).get("zitat") or "")
        if len(_norm(z)) < ZITAT_MIN:
            gruende.append(f"Zitat kürzer als {ZITAT_MIN} Zeichen")
        elif b.get("id"):
            d = docs.get(str(b["id"]))
            if d is None:
                gruende.append(f"{b['id']}: keine Nachricht der Historie")
            elif _norm(z) not in _norm(d.get("text", "")):
                gruende.append(f"{b['id']}: Zitat steht nicht wörtlich im Text")
            else:
                ok.append({"id": str(b["id"]), "zitat": z})
        elif b.get("url"):
            t = websuche.gelesen(str(b["url"]))
            if t is None:
                gruende.append(f"{b['url']}: Seite erst mit web_seite lesen")
            elif _norm(z) not in _norm(t):
                gruende.append(f"{b['url']}: Zitat steht nicht wörtlich auf der Seite")
            else:
                ok.append({"url": str(b["url"]), "zitat": z})
        else:
            gruende.append("Beleg braucht id (Nachricht) oder url (gelesene Seite)")
    return ok, gruende


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
    if art == "hashtag":
        grund = artname_fehler(voll.split(":", 1)[1])
        if grund:
            return {"abgelehnt": grund}
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


#: Tag-Arten, die niemand erfindet: feste Felder und Verknüpfungen (nachricht.FESTE_TAG_ARTEN, VERKNUEPFUNG)
NICHT_ERFINDBAR = frozenset({"quelle", "host", "befund", "incident"})


#: Namen, unter denen eine vorhandene Art schon existiert — gemessen 07.11.: die KI legte hashtag:standort an,
#: das ist ort (Identität: ein Ding, ein Name)
SYNONYME = {"standort": "ort", "location": "ort", "site": "ort", "rechenzentrum": "ort",
            "rechner": "host", "server": "host", "hostname": "host", "maschine": "host",
            "service": "dienst", "check": "dienst", "symptom": "thema", "kategorie": "thema",
            "pattern": "muster", "vorlage": "muster", "template": "muster",
            "kritikalität": "kritikalitaet", "criticality": "kritikalitaet", "hostgruppe": "hg", "gruppe": "hg",
            "anwendung": "app", "container": "app", "applikation": "app", "zustand": "zustand"}


def artname_fehler(name: str) -> str:
    """Grund, warum `name` keine neue Hashtag-Art sein kann — leer heißt: erlaubt. Rein.
    Eine Art ist eine Eigenschaft OHNE Wert (komponente, nicht komponente-sensecommon — gemessen 07.11.: die KI
    legte je Wert eine eigene Art an); der Wert steht hinter dem Doppelpunkt: komponente:sensecommon."""
    if not re.fullmatch(r"[a-z][a-z0-9]{1,30}", name or ""):
        teil = (name or "").split("-", 1)
        tipp = f" — Art {teil[0]}, Wert {teil[1]}: {teil[0]}:{teil[1]}" if len(teil) == 2 and teil[0] else ""
        return ("Name einer Hashtag-Art: ein Wort, klein, a-z und 0-9 (2–31 Zeichen) — die Eigenschaft, nicht "
                f"ihr Wert{tipp}")
    if name in NICHT_ERFINDBAR:
        return f"{name} ist ein festes Feld bzw. eine Verknüpfung"
    if name in SYNONYME and SYNONYME[name] != name:
        return f"{name} gibt es schon als Art {SYNONYME[name]} — nimm {SYNONYME[name]}:<wert> (ein Ding, ein Name)"
    if name in HASHTAG_ARTEN:
        return f"{name} ist eine Art des Systems — ihre Bedeutung steht schon in lw_feld"
    return ""


def erfindbar(registriert: set[str]) -> set[str]:
    """Registrierte Hashtag-Arten, die die KI im Befund frei belegen darf: nicht die des Systems
    (HASHTAG_ARTEN — deren Werte vergibt das Raster) und keine festen Felder. Rein."""
    return set(registriert) - set(HASHTAG_ARTEN) - NICHT_ERFINDBAR


async def registrierte_hashtag_arten() -> set[str]:
    """Arten mit Eintrag art=hashtag in feld_bedeutung (hashtag:<art> → <art>)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung
    async with AsyncSessionLocal() as db:
        namen = (await db.execute(select(FeldBedeutung.name).where(FeldBedeutung.art == "hashtag"))).scalars()
        return {n.split(":", 1)[1] for n in namen if ":" in n}


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



# ── Quellfelder (Feldkatalog): die KI beschreibt neue Felder selbst ─────────────
# Nutzer 07.11.: „wenn neue Felder dazukommen, müssen diese dokumentiert werden … die KI muss nur neue Felder +
# Beschreibung selber anlegen“. Auslöser: neue Graylog-Felder (servicename, error_type, event_action …) standen im
# Feldkatalog ohne Bedeutung.

#: so viele Felder beschreibt ein Lauf — der Job läuft nur, wenn kein Agent arbeitet (strata ist der Engpass)
QUELLFELDER_JE_LAUF = 10
#: so viele davon dürfen je Lauf zusätzlich im Web recherchiert werden (Nutzer 07.11.: „eventuell auch mit
#: einer Webrecherche — diese Fakten oder Zitate sind auch erlaubt“)
WEB_JE_LAUF = 3

PROMPT_QUELLFELD_WEB = """Du beschreibst ein Feld aus einem Log-System für eine Analyse-KI. Gegeben: das Feld und der
Text einer Webseite (Dokumentation, Forum). Antworte nur mit Fakten, die du belegen kannst — wie im
Recherche-Tool mit geprüften Zitaten. In Foren steht die Lösung meist im letzten Beitrag.
Antworte NUR mit JSON: {"bedeutung": "<1–2 Sätze Deutsch>", "belege": ["<wörtliches Zitat aus der Seite>", …],
"sicher": true|false}. Jedes Zitat muss wörtlich auf der Seite stehen; der Code prüft das. Passt die Seite nicht
zum Feld: sicher=false."""
#: Rollen, deren Felder beschrieben werden (ignorieren = Kennungen/Zeitstempel ohne Inhalt)
BESCHREIBEN_ROLLEN = ("entitaet", "kategorie", "kennung", "messwert", "freitext", "unbekannt")

PROMPT_QUELLFELD = """Du beschreibst ein Feld aus einem Log-System (Graylog, Wazuh oder CheckMK) für eine
Analyse-KI. Gegeben: Quelle, Feldname, Typ, Rolle und gemessene Beispielwerte mit Anzahl.
Antworte nur mit Fakten, die du belegen kannst — wie im Recherche-Tool mit geprüften Zitaten.
Antworte NUR mit JSON: {"bedeutung": "<1–2 Sätze Deutsch: was das Feld enthält und wofür es taugt>",
"belege": ["<wörtliches Zitat aus Feldname oder Beispielwerten, das die Bedeutung trägt>", …], "sicher": true|false}.
Jedes Zitat muss wörtlich im Feldnamen oder in den Beispielwerten stehen; der Code prüft das. sicher=true nur, wenn Name und Werte die Bedeutung eindeutig zeigen; sonst false
und keine Vermutung als Tatsache. Erfinde KEINE Bedeutung einzelner Werte oder Codes (z. B. „0=OK“), die nicht
aus den Beispielwerten selbst hervorgeht — gemessen 07.11.: host_state wurde mit den Service-Codes beschrieben."""


def quellfeld_prompt(quelle: str, feld: str, typ: str, rolle: str, beispiele: list) -> str:
    werte = ", ".join(f"{b.get('wert')!r} ({b.get('anzahl')})" for b in (beispiele or [])[:8]) or "keine"
    return f"Quelle: {quelle}\nFeld: {feld}\nTyp: {typ}\nRolle: {rolle}\nBeispielwerte (Anzahl): {werte}"


def pruefe_zitate(bedeutung: str, belege: list, quelltext: str) -> tuple[list[str], list[str]]:
    """Zitate einer Feldbeschreibung gegen den Quelltext (Feldname, Typ, Beispielwerte) prüfen. Rein — testbar.
    Rückgabe (geprüft, Gründe der Ablehnung). Eine Wert-Zuordnung in der Bedeutung („0=OK“, „1 = DOWN“) gilt nur,
    wenn sie selbst in einem geprüften Zitat steht — gemessen 07.11.: host_state bekam erfundene Service-Codes."""
    import re

    def n(x: str) -> str:
        return re.sub(r"\s+", " ", str(x or "")).strip().lower()
    q = n(quelltext)
    geprueft = [z for z in belege or [] if isinstance(z, str) and len(n(z)) >= 3 and n(z) in q]
    gruende = [f"Zitat steht nicht im Feldnamen oder den Beispielwerten: {z!r}" for z in belege or []
               if isinstance(z, str) and z not in geprueft]
    if not geprueft:
        gruende.append("kein geprüftes Zitat")
    for m in re.finditer(r"\b\d+\s*=\s*[A-Za-zÄÖÜäöü]+", bedeutung or ""):
        if not any(n(m.group(0)) in n(z) for z in geprueft):
            gruende.append(f"Wert-Zuordnung {m.group(0)!r} ohne Zitat")
    return geprueft, gruende


def lies_antwort(raw: str) -> dict | None:
    """JSON-Antwort des Modells lesen (auch in ```-Blöcken). Rein — testbar."""
    import json
    import re
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d.get("bedeutung"), str) or not isinstance(d.get("sicher"), bool):
        return None
    return d


async def offene_quellfelder(grenze: int = QUELLFELDER_JE_LAUF) -> tuple[list, int]:
    """Aktive Quellfelder ohne Bedeutung, meistgenutzte zuerst. Rückgabe (Felder, Gesamtzahl offen)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung, FieldCatalog
    async with AsyncSessionLocal() as db:
        await stelle_bereit(db)
        bekannt = set((await db.execute(select(FeldBedeutung.name).where(FeldBedeutung.art == "quellfeld"))).scalars())
        rows = (await db.execute(select(FieldCatalog).where(FieldCatalog.status == "aktiv",
                                                            FieldCatalog.rolle.in_(BESCHREIBEN_ROLLEN))
                                 .order_by(FieldCatalog.abdeckung.desc().nullslast()))).scalars().all()
    offen = [r for r in rows if f"quellfeld:{r.quelle}.{r.feld}" not in bekannt]
    return offen[:grenze], len(offen)


async def beschreibe_quellfelder(llm=None, grenze: int = QUELLFELDER_JE_LAUF) -> dict:
    """Die KI beschreibt neue Quellfelder (nur sichere Bedeutungen; unsichere bleiben benannt offen)."""
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_active_llm_config
    if llm is None:
        from app.services.llm_client import generate_text as llm
    async with AsyncSessionLocal() as db:
        cfg = await get_active_llm_config(db)
    felder, offen = await offene_quellfelder(grenze)
    erg = {"offen_vorher": offen, "angelegt": [], "aus_web": [], "unsicher": [], "unbelegt": [], "web_ohne_beleg": [],
           "fehler": []}
    for f in felder:
        try:
            raw = await llm(cfg, [{"role": "system", "content": PROMPT_QUELLFELD},
                                  {"role": "user", "content": quellfeld_prompt(f.quelle, f.feld, f.typ, f.rolle,
                                                                               f.beispielwerte or [])}],
                            temperature=0.1, reasoning_effort="low")
        except Exception as e:  # noqa: BLE001
            erg["fehler"].append({"feld": f"{f.quelle}.{f.feld}", "fehler": f"{type(e).__name__}: {str(e)[:120]}"})
            continue
        a = lies_antwort(raw)
        quelltext = quellfeld_prompt(f.quelle, f.feld, f.typ, f.rolle, f.beispielwerte or [])
        geprueft, gruende = (pruefe_zitate(a["bedeutung"], a.get("belege") or [], quelltext)
                             if a and a["sicher"] else ([], ["unsicher"]))
        if gruende:
            # zweite Stufe: Webrecherche — Zitate aus der gelesenen Seite sind ebenfalls Belege
            if len(erg["aus_web"]) + len(erg["web_ohne_beleg"]) < WEB_JE_LAUF:
                w = await _aus_dem_web(llm, cfg, f, quelltext)
                if w.get("angelegt"):
                    erg["aus_web"].append(w["angelegt"])
                    continue
                erg["web_ohne_beleg"].append({"feld": f"{f.quelle}.{f.feld}", "grund": w.get("grund")})
            (erg["unsicher"] if gruende == ["unsicher"] else erg["unbelegt"]).append(
                f"{f.quelle}.{f.feld}" if gruende == ["unsicher"] else {"feld": f"{f.quelle}.{f.feld}",
                                                                           "gruende": gruende[:3]})
            continue
        bsp = str((f.beispielwerte or [{}])[0].get("wert", ""))[:200]
        r = await anlegen(f"{f.quelle}.{f.feld}", "quellfeld", a["bedeutung"], bsp,
                          f"Feldkatalog (Name, Typ {f.typ}, Rolle {f.rolle}, Beispielwerte); geprüfte Zitate: "
                          + "; ".join(f"„{z}“" for z in geprueft) + f" (Modell {getattr(cfg, 'model', '?')})", "ki")
        (erg["angelegt"] if "angelegt" in r else erg["fehler"]).append(r.get("angelegt") or r)
    return erg



async def korrigiere(name: str, bedeutung: str, quelle: str, von: str) -> dict:
    """Eine vorhandene Bedeutung korrigieren — nur Mensch oder System (die KI legt nur Neues an). Die alte
    Bedeutung bleibt in der Quelle nachlesbar („ersetzt: …“)."""
    if von == "ki":
        return {"abgelehnt": "die KI korrigiert keine vorhandenen Bedeutungen"}
    if len((bedeutung or "").strip()) < 10 or not (quelle or "").strip():
        return {"abgelehnt": "Bedeutung (ein Satz) und Quelle sind Pflicht"}
    from datetime import datetime, timezone

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung
    async with AsyncSessionLocal() as db:
        e = await db.get(FeldBedeutung, name)
        if e is None:
            return {"abgelehnt": f"{name} gibt es nicht"}
        alt = e.bedeutung
        e.bedeutung, e.herkunft = bedeutung.strip()[:2000], "mensch" if von != "system" else "system"
        e.quelle = f"{quelle.strip()} — ersetzt ({e.angelegt_von}): {alt}"[:2000]
        e.angelegt_von, e.angelegt_at = von, datetime.now(timezone.utc)
        await db.commit()
    return {"korrigiert": name, "vorher": alt}



async def _aus_dem_web(llm, cfg, f, quelltext: str) -> dict:
    """Ein Quellfeld per Webrecherche beschreiben: suchen, die beste Seite lesen, beschreiben lassen — nur mit
    Zitaten, die wörtlich auf der Seite stehen (websuche.gelesen). Rückgabe {angelegt} oder {grund}."""
    from app.services.log_watch import websuche
    produkt = {"checkmk": "Checkmk", "wazuh": "Wazuh", "graylog": "Graylog"}.get(f.quelle, f.quelle)
    s = await websuche.suche(f"{produkt} {f.feld} field", f"Bedeutung des Quellfelds {f.quelle}.{f.feld} unklar")
    treffer = [t for t in s.get("treffer") or [] if t.get("url", "").startswith("http")]
    if not treffer:
        return {"grund": s.get("fehler") or "keine Treffer"}
    for t in treffer[:2]:
        seite = await websuche.seite(t["url"])
        voll = websuche.gelesen(t["url"])
        if not voll:
            continue
        raw = await llm(cfg, [{"role": "system", "content": PROMPT_QUELLFELD_WEB},
                              {"role": "user", "content": f"{quelltext}\n\nSeite {t['url']}:\n{seite.get('text', '')}"}],
                        temperature=0.1, reasoning_effort="low")
        a = lies_antwort(raw)
        if not a or not a["sicher"]:
            continue
        geprueft, gruende = pruefe_zitate(a["bedeutung"], a.get("belege") or [], voll + "\n" + quelltext)
        if gruende:
            continue
        r = await anlegen(f"{f.quelle}.{f.feld}", "quellfeld", a["bedeutung"],
                          str((f.beispielwerte or [{}])[0].get("wert", ""))[:200],
                          f"Webrecherche {t['url']}; geprüfte Zitate: " + "; ".join(f"„{z}“" for z in geprueft)
                          + f" (Modell {getattr(cfg, 'model', '?')})", "ki")
        if r.get("angelegt"):
            return r
    return {"grund": "keine Seite mit geprüftem Zitat"}
