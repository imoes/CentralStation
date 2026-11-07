"""Regelwerk wie eine Firewall: geordnete Regeln, die ERSTE passende entscheidet FORWARD
(relevant — in die Historie, zur KI) oder BLOCK (nicht relevant). Am Ende steht die
Default-Policy BLOCK (Kriterium N0 „kein Kriterium erfüllt“).

Eine Regel prüft nur Merkmale, die jede Quelle als Hashtags setzt (`quelle:`, `host:`,
`zustand:`, `kritikalitaet:`, `regel:`, `signal:neu` …) und wenige Felder (`level`). Damit
filtert eine Regel genau das, was ein Mensch in der Suche filtert — ein Ding, ein Name.

Jede Regel gehört zu einem Kriterium der Relevanz-Definition (definition.py); das Urteil
zitiert die entscheidende Regel und weitere passende Regeln derselben Aktion. Regeln im
Modus `test` entscheiden nicht, sie zählen nur, was sie träfen. Eine aktive Regel, die
passt, aber nie entscheidet, weil eine frühere es tut, ist verdeckt — das wird gezählt,
nicht vermutet.

Bedingung (JSON), alle Klauseln UND-verknüpft:
  {"alle": [{"hat": "signal:neu"}, {"eines_von": ["kritikalitaet:prod", …]},
            {"praefix": ["dienst:Check_MK"]}, {"feld": "level", "ab": 7}, {"nicht": {…}}]}
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

FORWARD, BLOCK = "forward", "block"
#: dritte Aktion (Nutzer 07.11.: Incidents nach der Logik des Regelwerks): eine Incident-Regel fasst die
#: Nachrichten ihres Filters je Gruppe in einem Zeitfenster zusammen und eröffnet bei Erreichen der
#: Mindestwerte einen Incident. Im Urteil je Nachricht (bewerte) zählt sie nicht.
INCIDENT = "incident"
AKTIONEN = (FORWARD, BLOCK, INCIDENT)
#: Gruppierung einer Incident-Regel — erschöpfend: Muster (über Hosts), Dienst (über Hosts), Host
GRUPPEN = ("muster", "dienst", "host")
#: fenster = verschiedene Fünf-Minuten-Fenster mit Treffern (wie error_clusters/critical: „3 Ereignisse in 6 h“)
MINDESTENS = ("nachrichten", "hosts", "quellen", "fenster")
#: Art des Incidents, den eine Regel eröffnet (Incident.kind)
INCIDENT_ARTEN = ("log_cluster", "fleet", "critical", "host")
AKTIV, TEST = "aktiv", "test"

#: Signale, die die Quellen setzen (Hashtag `signal:<name>`) — erschöpfend benannt
SIGNALE = ("stumm", "behandelt", "schaden", "frist", "neu", "ausbreitung", "anstieg", "dringend",
           "bedrohung")


@dataclass(frozen=True)
class Regel:
    id: str
    kriterium: str
    name: str
    aktion: str                       # forward | block
    bedingung: dict
    modus: str = AKTIV                # aktiv | test
    herkunft: str = "system"          # system | admin | ki
    #: Stufe bei BLOCK: 0 = Rauschen/selbst erzeugt/stumm, 1 = sonst
    stufe_block: int = 1
    #: Filtertext (filter.py), aus dem `bedingung` übersetzt ist — die Form, die Menschen
    #: lesen und schreiben; leer nur bei direkt angegebener Bedingung
    filter: str = ""
    #: nur bei aktion=incident: Gruppe, Fenster in Minuten, Mindestwerte je Gruppe, Art des Incidents
    gruppe: str = ""
    fenster_min: int = 0
    mindestens: dict = field(default_factory=dict)
    incident_art: str = ""


@dataclass
class Entscheidung:
    aktion: str
    regel: str                        # entscheidende Regel-ID („default“ = Default-Policy)
    kriterien: list[str]              # entscheidendes Kriterium zuerst, dann weitere gleicher Aktion
    stufe_block: int = 1
    #: aktive Regeln, die passten, aber nicht entschieden (verdeckt in diesem Fall)
    verdraengt: list[str] = field(default_factory=list)
    #: Regeln im Testmodus, die gepasst hätten
    test_treffer: list[str] = field(default_factory=list)


def passt(bedingung: dict, tags: set[str], felder: dict) -> bool:
    """Reine Auswertung einer Bedingung. Unbekannte Klauseln passen NIE (sicher: lieber
    eine Regel, die nicht greift, als eine, die alles greift)."""
    if "alle" in bedingung:
        return all(passt(k, tags, felder) for k in bedingung["alle"])
    if "irgendeins" in bedingung:
        return any(passt(k, tags, felder) for k in bedingung["irgendeins"])
    if "hat" in bedingung:
        return bedingung["hat"] in tags
    if "eines_von" in bedingung:
        return bool(tags & set(bedingung["eines_von"]))
    if "praefix" in bedingung:
        pr = bedingung["praefix"]
        praefixe = (pr,) if isinstance(pr, str) else tuple(pr)
        return any(t.startswith(praefixe) for t in tags) if praefixe else False
    if "nicht" in bedingung:
        return not passt(bedingung["nicht"], tags, felder)
    if "feld" in bedingung:
        v = felder.get(bedingung["feld"])
        if not isinstance(v, (int, float)):
            return False
        if "ab" in bedingung and v < bedingung["ab"]:
            return False
        if "bis" in bedingung and v > bedingung["bis"]:
            return False
        return "ab" in bedingung or "bis" in bedingung
    return False


def bewerte(regeln: list[Regel], tags: set[str], felder: dict | None = None) -> Entscheidung:
    """Erste passende aktive Regel entscheidet; ohne Treffer gilt die Default-Policy."""
    felder = felder or {}
    entscheidend: Regel | None = None
    passende: list[Regel] = []
    test: list[str] = []
    for r in regeln:
        if r.aktion == INCIDENT:          # zählt nicht im Urteil je Nachricht (incident_regeln.py)
            continue
        if not passt(r.bedingung, tags, felder):
            continue
        if r.modus == TEST:
            test.append(r.id)
            continue
        passende.append(r)
        if entscheidend is None:
            entscheidend = r
    if entscheidend is None:
        return Entscheidung(BLOCK, "default", ["N0"], 1, [], test)
    gleich = [r.kriterium for r in passende if r.aktion == entscheidend.aktion]
    return Entscheidung(entscheidend.aktion, entscheidend.id, list(dict.fromkeys(gleich)),
                        entscheidend.stufe_block,
                        [r.id for r in passende if r is not entscheidend], test)


#: Standardregeln als Filtertexte (filter.py) — Reihenfolge = vereinbarter Vorrang
#: (N6 > N2 > N4 > R5/R6 vor N3/N5 > N3/N5 vor übrigen R > R vor N1 > Default N0).
#: (id, kriterium, name, aktion, filter, stufe_block)
STANDARD_FILTER = (
    ("N6", "N6", "Stumm geschaltet", BLOCK, "signal:stumm", 0),
    ("N2", "N2", "Bekannt und behandelt", BLOCK, "signal:behandelt", 1),
    ("N4", "N4", "Rauschen", BLOCK, "@Rauschen", 0),
    ("R5", "R5", "Schaden an Ressourcen", FORWARD, "signal:schaden", 1),
    ("R6", "R6", "Sicherheit", FORWARD, "quelle:wazuh level>=7", 1),
    ("R6-FW", "R6", "Firewall-Bedrohung", FORWARD, "quelle:firewall signal:bedrohung", 1),
    ("N3", "N3", "Nicht-Produktion", BLOCK, "@Nicht-Produktion", 1),
    ("N5", "N5", "Selbst erzeugt", BLOCK, "@Selbst-erzeugt", 0),
    ("R1", "R1", "Störung in Produktion", FORWARD, "zustand:crit @Produktion", 1),
    ("R2", "R2", "Neues Muster", FORWARD, "signal:neu", 1),
    ("R3", "R3", "Ausbreitung", FORWARD, "signal:ausbreitung", 1),
    ("R4", "R4", "Anstieg", FORWARD, "signal:anstieg", 1),
    ("R8", "R8", "Ablaufende Frist", FORWARD, "signal:frist", 1),
    ("N1", "N1", "Routine", BLOCK, "@Routine-Quellen", 1),
)


#: Incident-Regeln — die vier Wege, auf denen heute Incidents entstehen (error_clusters), als Filter +
#: Gruppe + Fenster + Mindestwerte. Zuerst im Testmodus: sie zählen, was sie eröffnet hätten, und
#: incident_regeln.py vergleicht das mit den Incidents der alten Schwellen (Schattenbetrieb).
#: (id, kriterium, name, filter, gruppe, fenster_min, mindestens, incident_art)
STANDARD_INCIDENT = (
    # error_clusters/runner._handle: Sofort-Meldung eines Musters (spread, persistent, große Spitze, großes Neues)
    ("I-AUSBRUCH", "R4", "Fehlerausbruch eines Musters", "quelle:graylog signal:dringend !signal:schaden",
     "muster", 30, {"nachrichten": 1}, "log_cluster"),
    # error_clusters/fleet: derselbe CheckMK-Dienst auf ≥ 5 Hosts (alt zusätzlich ≥ 3× Median — fehlt hier)
    ("I-FLOTTE", "R3", "Flotten-Ausfall eines Dienstes", "quelle:checkmk zustand:crit",
     "dienst", 45, {"hosts": 5}, "fleet"),
    # error_clusters/critical: kritische Kernelmeldung wiederholt — ≥ 3 Fünf-Minuten-Fenster in 6 h, wie alt
    ("I-KRITISCH", "R5", "Kritische Kernelmeldung gehäuft", "quelle:graylog signal:schaden",
     "host", 360, {"fenster": 3}, "critical"),
    # error_clusters/incident.process_alerts: ≥ 2 kritische/hohe Alerts oder ≥ 2 Quellen auf einem Host in
    # 30 min — hier ≥ 2 dringende Nachrichten (deckt beides; gemessen 07.11.: mit „2 Quellen“ 16 nur alt).
    # Unterschied mit Absicht: nur, was das Regelwerk relevant nennt (kein Testhost, kein Rauschen)
    ("I-HOST", "R1", "Host mit mehreren dringenden Meldungen", "relevanz>=3",
     "host", 30, {"nachrichten": 2}, "host"),
)


def standard_regeln(objekte: dict[str, str]) -> list[Regel]:
    """Standardregeln, übersetzt mit den Objekten der Definition — Urteilsregeln, dann Incident-Regeln."""
    from app.services.log_watch.filter import bedingung
    return ([Regel(rid, krit, name, aktion, bedingung(text, objekte), stufe_block=stufe, filter=text)
             for rid, krit, name, aktion, text, stufe in STANDARD_FILTER]
            + [Regel(rid, krit, name, INCIDENT, bedingung(text, objekte), modus=TEST, filter=text,
                     gruppe=gr, fenster_min=fm, mindestens=dict(mi), incident_art=art)
               for rid, krit, name, text, gr, fm, mi, art in STANDARD_INCIDENT])


def als_daten(regeln: list[Regel]) -> list[dict]:
    return [asdict(r) for r in regeln]


def aus_daten(daten: list[dict], objekte: dict[str, str] | None = None) -> list[Regel]:
    """Regeln aus Daten; ein Filtertext wird mit den Objekten (neu) übersetzt."""
    from app.services.log_watch.filter import bedingung
    out = []
    for d in daten:
        werte = {k: d[k] for k in Regel.__dataclass_fields__ if k in d}
        if werte.get("filter"):
            werte["bedingung"] = bedingung(werte["filter"], objekte or {})
        out.append(Regel(**werte))
    return out


def pruefe(daten: list[dict], kriterien_ids: set[str], objekte: dict[str, str] | None = None) -> list[str]:
    """Fehler eines Regelsatzes — leer heißt gültig."""
    from app.services.log_watch.filter import FilterFehler, bedingung
    fehler = []
    ids = [d.get("id") for d in daten]
    if len(ids) != len(set(ids)):
        fehler.append("Regel-IDs doppelt")
    for d in daten:
        rid = d.get("id")
        if d.get("aktion") not in AKTIONEN:
            fehler.append(f"{rid}: aktion muss forward, block oder incident sein")
        if d.get("aktion") == INCIDENT:
            if d.get("gruppe") not in GRUPPEN:
                fehler.append(f"{rid}: gruppe muss {', '.join(GRUPPEN)} sein")
            if not isinstance(d.get("fenster_min"), int) or d["fenster_min"] <= 0:
                fehler.append(f"{rid}: fenster_min muss eine positive Zahl (Minuten) sein")
            mi = d.get("mindestens") or {}
            if not mi or set(mi) - set(MINDESTENS) or not all(isinstance(v, int) and v >= 1 for v in mi.values()):
                fehler.append(f"{rid}: mindestens braucht {', '.join(MINDESTENS)} als ganze Zahlen ≥ 1")
            if d.get("incident_art") not in INCIDENT_ARTEN:
                fehler.append(f"{rid}: incident_art muss {', '.join(INCIDENT_ARTEN)} sein")
        if d.get("modus", AKTIV) not in (AKTIV, TEST):
            fehler.append(f"{rid}: modus muss aktiv oder test sein")
        if d.get("kriterium") not in kriterien_ids:
            fehler.append(f"{rid}: Kriterium {d.get('kriterium')} gibt es in der Definition nicht")
        if d.get("filter"):
            try:
                bedingung(d["filter"], objekte or {})
            except FilterFehler as e:
                fehler.append(f"{rid}: Filter {d['filter']!r}: {e}")
        elif not isinstance(d.get("bedingung"), dict) or not _wohlgeformt(d["bedingung"]):
            fehler.append(f"{rid}: Bedingung nicht wohlgeformt")
    return fehler


def _wohlgeformt(b: dict) -> bool:
    for verbund in ("alle", "irgendeins"):
        if verbund in b:
            return isinstance(b[verbund], list) and bool(b[verbund]) and all(
                isinstance(k, dict) and _wohlgeformt(k) for k in b[verbund])
    if "hat" in b:
        return isinstance(b["hat"], str)
    if "praefix" in b:
        pr = b["praefix"]
        return isinstance(pr, str) or (isinstance(pr, list) and all(isinstance(x, str) for x in pr))
    if "eines_von" in b:
        return isinstance(b["eines_von"], list) and all(isinstance(x, str) for x in b["eines_von"])
    if "nicht" in b:
        return isinstance(b["nicht"], dict) and _wohlgeformt(b["nicht"])
    if "feld" in b:
        return isinstance(b["feld"], str) and ("ab" in b or "bis" in b)
    return False
