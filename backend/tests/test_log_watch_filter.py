"""Filtersprache: Syntax, Fehler mit Stelle, Objekte (gespeicherte Suchen), und der Beweis,
dass Regelwerk-Bedingung und OpenSearch-Abfrage aus demselben Filter dieselben Nachrichten
treffen."""
import random

import pytest

from app.services.log_watch import filter as f
from app.services.log_watch.regelwerk import passt


# ── Syntax ──────────────────────────────────────────────────────────────────────

def test_syntax_beispiele():
    assert f.bedingung("thema:oom") == {"hat": "thema:oom"}
    assert f.bedingung("kritikalitaet:(prod OR staging)") == {"eines_von": ["kritikalitaet:prod",
                                                                            "kritikalitaet:staging"]}
    assert f.bedingung("dienst:Check_MK*") == {"praefix": "dienst:Check_MK"}
    assert f.bedingung('mitre:"Credential Access"') == {"hat": "mitre:Credential Access"}
    assert f.bedingung("quelle:wazuh level>=7") == {"alle": [{"hat": "quelle:wazuh"},
                                                             {"feld": "level", "ab": 7}]}
    assert f.bedingung("!signal:stumm") == {"nicht": {"hat": "signal:stumm"}}
    assert f.bedingung("NOT signal:stumm AND thema:oom") == {
        "alle": [{"nicht": {"hat": "signal:stumm"}}, {"hat": "thema:oom"}]}
    assert f.bedingung("(thema:oom OR signal:schaden) quelle:graylog") == {
        "alle": [{"eines_von": ["thema:oom", "signal:schaden"]}, {"hat": "quelle:graylog"}]}
    assert f.bedingung("thema:oom OR level>=7") == {          # gemischt: allgemeines ODER
        "irgendeins": [{"hat": "thema:oom"}, {"feld": "level", "ab": 7}]}


@pytest.mark.parametrize("text,stelle", [
    ("(thema:oom", r"„\)“ fehlt"),
    ("kritikalitaet:(prod", r"„\)“ fehlt"),
    ("kritikalitaet:()", "ist leer"),
    ("tiefe>=3", "nicht vergleichbar"),
    ("", "leerer Filter"),
    ("thema:oom )", "unerwartet"),
])
def test_fehler_mit_grund(text, stelle):
    with pytest.raises(f.FilterFehler, match=stelle):
        f.bedingung(text)


def test_freitext_nur_in_der_suche():
    assert f.opensearch('"connection refused" host:x')["bool"]["must"] == [
        {"match_phrase": {"text": "connection refused"}}]
    with pytest.raises(f.FilterFehler, match="in Regeln nicht erlaubt"):
        f.bedingung("timeout host:x")


# ── Objekte = gespeicherte Suchen ──────────────────────────────────────────────

OBJ = {"Produktion": "kritikalitaet:(prod OR staging OR critical)",
       "Kritisch-Prod": "zustand:crit @Produktion",
       "A": "@B", "B": "@A"}


def test_objekte_aufloesen_verschachtelt():
    assert f.bedingung("@Kritisch-Prod", OBJ) == {"alle": [
        {"hat": "zustand:crit"},
        {"eines_von": ["kritikalitaet:prod", "kritikalitaet:staging", "kritikalitaet:critical"]}]}


def test_objekt_zyklus_und_unbekannt_benannt():
    with pytest.raises(f.FilterFehler, match="Zyklus: A → B → A"):
        f.bedingung("@A", OBJ)
    with pytest.raises(f.FilterFehler, match="@Fehlt gibt es nicht"):
        f.bedingung("@Fehlt", OBJ)


# ── Beweis: beide Übersetzungen treffen dieselben Nachrichten ─────────────────

def os_trifft(q: dict, doc: dict) -> bool:
    """Kleiner Interpreter der OpenSearch-Bausteine, die als_opensearch erzeugt."""
    if "bool" in q:
        b = q["bool"]
        if not all(os_trifft(x, doc) for x in b.get("filter", []) + b.get("must", [])):
            return False
        if any(os_trifft(x, doc) for x in b.get("must_not", [])):
            return False
        if "should" in b:
            return any(os_trifft(x, doc) for x in b["should"])
        return True
    if "term" in q:
        (feld, wert), = q["term"].items()
        return wert in doc["hashtags"] if feld == "hashtags" else doc.get(feld) == wert
    if "prefix" in q:
        return any(t.startswith(q["prefix"]["hashtags"]) for t in doc["hashtags"])
    if "range" in q:
        (feld, r), = q["range"].items()
        v = doc.get(feld)
        if v is None:
            return False
        return all({"gte": v >= z, "lte": v <= z, "gt": v > z, "lt": v < z}[op] for op, z in r.items())
    raise AssertionError(q)


TAGS = ["thema:oom", "thema:dns", "quelle:wazuh", "quelle:graylog", "kritikalitaet:prod",
        "kritikalitaet:test", "dienst:Check_MK Discovery", "signal:neu", "signal:stumm"]
ATOME = ["thema:oom", "thema:dns", "quelle:wazuh", "kritikalitaet:(prod OR test)", "dienst:Check_MK*",
         "signal:neu", "signal:stumm", "level>=7", "level<5", "level=9", "@Produktion"]


def _zufallsfilter(rnd: random.Random, tiefe=0) -> str:
    if tiefe > 2 or rnd.random() < 0.35:
        a = rnd.choice(ATOME)
        return ("!" + a) if rnd.random() < 0.2 else a
    teile = [_zufallsfilter(rnd, tiefe + 1) for _ in range(rnd.randint(2, 3))]
    return "(" + (" OR " if rnd.random() < 0.5 else " ").join(teile) + ")"


def test_regelwerk_und_opensearch_treffen_dasselbe():
    rnd = random.Random(7)
    objekte = {"Produktion": "kritikalitaet:(prod OR staging)"}
    docs = []
    for _ in range(300):
        tags = set(rnd.sample(TAGS, rnd.randint(0, 5)))
        level = rnd.choice([None, 3, 5, 7, 9, 12])
        docs.append({"hashtags": tags, **({"level": level} if level is not None else {})})
    geprueft = 0
    for _ in range(400):
        text = _zufallsfilter(rnd)
        k = f.aufloesen(f.lies(text), objekte)
        bed, q = f.als_bedingung(k), f.als_opensearch(k)
        for d in docs:
            felder = {"level": d["level"]} if "level" in d else {}
            assert passt(bed, d["hashtags"], felder) == os_trifft(q, d), (text, d)
            geprueft += 1
    assert geprueft == 400 * 300
