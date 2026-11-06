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
AKTIV, TEST = "aktiv", "test"

#: Signale, die die Quellen setzen (Hashtag `signal:<name>`) — erschöpfend benannt
SIGNALE = ("stumm", "behandelt", "schaden", "frist", "neu", "ausbreitung", "anstieg", "dringend")


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


def standard_regeln(p) -> list[Regel]:
    """Version 1 der Definition als Regelwerk — Reihenfolge = bisheriger Vorrang
    (N6 > N2 > N4 > R5/R6 vor N3/N5 > N3/N5 vor übrigen R > R vor N1 > Default N0).
    `p(name)` liefert Parameter der Definition."""
    rel = [f"kritikalitaet:{k}" for k in p("relevante_kritikalitaeten")]
    nicht_prod = [f"kritikalitaet:{k}" for k in p("nicht_produktion")]
    rauschen = ([f"regel:{r}" for r in p("rauschen_regeln")]
                + [f"regelgruppe:{g}" for g in p("rauschen_regelgruppen")])
    selbst = [f"dienst:{d}" for d in p("selbst_dienste")]
    return [
        Regel("N6", "N6", "Stumm geschaltet", BLOCK, {"hat": "signal:stumm"}, stufe_block=0),
        Regel("N2", "N2", "Bekannt und behandelt", BLOCK, {"hat": "signal:behandelt"}),
        Regel("N4", "N4", "Rauschen", BLOCK, {"eines_von": rauschen}, stufe_block=0),
        Regel("R5", "R5", "Schaden an Ressourcen", FORWARD, {"hat": "signal:schaden"}),
        Regel("R6", "R6", "Sicherheit", FORWARD,
              {"alle": [{"hat": "quelle:wazuh"}, {"feld": "level", "ab": p("wazuh_level_ab")}]}),
        Regel("N3", "N3", "Nicht-Produktion", BLOCK, {"eines_von": nicht_prod}),
        Regel("N5", "N5", "Selbst erzeugt", BLOCK, {"praefix": selbst}, stufe_block=0),
        Regel("R1", "R1", "Störung in Produktion", FORWARD,
              {"alle": [{"hat": "zustand:crit"}, {"eines_von": rel}]}),
        Regel("R2", "R2", "Neues Muster", FORWARD, {"hat": "signal:neu"}),
        Regel("R3", "R3", "Ausbreitung", FORWARD, {"hat": "signal:ausbreitung"}),
        Regel("R4", "R4", "Anstieg", FORWARD, {"hat": "signal:anstieg"}),
        Regel("R8", "R8", "Ablaufende Frist", FORWARD, {"hat": "signal:frist"}),
        Regel("N1", "N1", "Routine", BLOCK, {"hat": "quelle:graylog"}),
    ]


def als_daten(regeln: list[Regel]) -> list[dict]:
    return [asdict(r) for r in regeln]


def aus_daten(daten: list[dict]) -> list[Regel]:
    return [Regel(**{k: d[k] for k in Regel.__dataclass_fields__ if k in d}) for d in daten]


def pruefe(daten: list[dict], kriterien_ids: set[str]) -> list[str]:
    """Fehler eines Regelsatzes — leer heißt gültig."""
    fehler = []
    ids = [d.get("id") for d in daten]
    if len(ids) != len(set(ids)):
        fehler.append("Regel-IDs doppelt")
    for d in daten:
        rid = d.get("id")
        if d.get("aktion") not in (FORWARD, BLOCK):
            fehler.append(f"{rid}: aktion muss forward oder block sein")
        if d.get("modus", AKTIV) not in (AKTIV, TEST):
            fehler.append(f"{rid}: modus muss aktiv oder test sein")
        if d.get("kriterium") not in kriterien_ids:
            fehler.append(f"{rid}: Kriterium {d.get('kriterium')} gibt es in der Definition nicht")
        if not isinstance(d.get("bedingung"), dict) or not _wohlgeformt(d["bedingung"]):
            fehler.append(f"{rid}: Bedingung nicht wohlgeformt")
    return fehler


def _wohlgeformt(b: dict) -> bool:
    if "alle" in b:
        return isinstance(b["alle"], list) and bool(b["alle"]) and all(
            isinstance(k, dict) and _wohlgeformt(k) for k in b["alle"])
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
