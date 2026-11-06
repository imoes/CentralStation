"""Eine Filtersprache für Suche, Objekte, Regeln, Netzplan und MCP.

Ein Objekt ist eine gespeicherte Suche, eine Regel ist eine Suche mit Aktion (Nutzer,
06.11.). Darum gibt es genau EINE Sprache und aus einem Syntaxbaum zwei Übersetzungen:
`als_bedingung` (Regelwerk, im Speicher je Urteil) und `als_opensearch` (Suche, Netzplan,
Trefferzahl, Testmodus). Ein Test belegt, dass beide dieselben Nachrichten treffen.

Syntax (Leerzeichen = UND):
  art:wert            Hashtag             thema:oom
  art:(a OR b)        einer von           kritikalitaet:(prod OR staging)
  art:wert*           Präfix              dienst:Check_MK*
  art:"mit Leerzeichen"                   mitre:"Credential Access"
  feld>=7 / <= / = / > / <                level>=7
  @Name               Objekt (gespeicherte Suche)
  !x  NOT x           nicht
  a OR b, ( … )       oder, Klammern
  wort / "wörter"     Freitext — nur in der Suche, nicht in Regeln
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Felder, die ein Vergleich prüfen darf — im Regelwerk (Treffer.felder) wie im Index
FELDER = ("level", "relevanz")


class FilterFehler(ValueError):
    """Filter nicht lesbar oder für diesen Zweck nicht erlaubt — mit Stelle und Grund."""


@dataclass(frozen=True)
class Knoten:
    art: str                      # und | oder | nicht | tag | praefix | feld | objekt | text
    kinder: tuple = ()
    wert: str = ""
    op: str = ""
    zahl: float = 0.0


_TOKEN = re.compile(r'''
    (?P<ws>\s+)
  | (?P<klauf>\()
  | (?P<klzu>\))
  | (?P<nicht>!)
  | (?P<feld>[A-Za-z_][\w.]*\s*(>=|<=|>|<|=)\s*-?\d+(\.\d+)?)
  | (?P<artwert>[A-Za-z_][\w.\-]*:(\(|"[^"]*"\*?|[^\s()"]+))
  | (?P<objekt>@[\w.\-ÄÖÜäöüß]+)
  | (?P<zitat>"[^"]*")
  | (?P<wort>[^\s()"]+)
''', re.VERBOSE)


def _tokens(text: str) -> list[tuple[str, str, int]]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise FilterFehler(f"Zeichen {pos + 1}: nicht lesbar: {text[pos:pos + 20]!r}")
        art = m.lastgroup
        if art != "ws":
            out.append((art, m.group(0), pos))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.t = _tokens(text)
        self.i = 0

    def _sieh(self):
        return self.t[self.i] if self.i < len(self.t) else (None, "", len(self.text))

    def _nimm(self):
        tok = self._sieh()
        self.i += 1
        return tok

    def ausdruck(self) -> Knoten:
        teile = [self.und()]
        while self._sieh()[0] == "wort" and self._sieh()[1] == "OR":
            self._nimm()
            teile.append(self.und())
        return teile[0] if len(teile) == 1 else Knoten("oder", tuple(teile))

    def und(self) -> Knoten:
        teile = []
        while True:
            art, wert, _ = self._sieh()
            if art is None or art == "klzu" or (art == "wort" and wert == "OR"):
                break
            if art == "wort" and wert == "AND":
                self._nimm()
                continue
            teile.append(self.einfach())
        if not teile:
            raise FilterFehler(f"Zeichen {self._sieh()[2] + 1}: Ausdruck erwartet")
        return teile[0] if len(teile) == 1 else Knoten("und", tuple(teile))

    def einfach(self) -> Knoten:
        art, wert, pos = self._nimm()
        if art == "nicht" or (art == "wort" and wert == "NOT"):
            return Knoten("nicht", (self.einfach(),))
        if art == "klauf":
            k = self.ausdruck()
            if self._nimm()[0] != "klzu":
                raise FilterFehler(f"Zeichen {pos + 1}: „)“ fehlt")
            return k
        if art == "feld":
            m = re.match(r"([A-Za-z_][\w.]*)\s*(>=|<=|>|<|=)\s*(-?\d+(?:\.\d+)?)", wert)
            name, op, zahl = m.group(1), m.group(2), float(m.group(3))
            if name not in FELDER:
                raise FilterFehler(f"Zeichen {pos + 1}: Feld {name} ist nicht vergleichbar "
                                   f"(erlaubt: {', '.join(FELDER)})")
            return Knoten("feld", wert=name, op=op, zahl=zahl)
        if art == "artwert":
            name, rest = wert.split(":", 1)
            if rest == "(":
                return self._liste(name, pos)
            return _wert(name, rest)
        if art == "objekt":
            return Knoten("objekt", wert=wert[1:])
        if art in ("zitat", "wort"):
            return Knoten("text", wert=wert.strip('"'))
        raise FilterFehler(f"Zeichen {pos + 1}: unerwartet {wert!r}")

    def _liste(self, name: str, pos: int) -> Knoten:
        werte = []
        while True:
            art, wert, p = self._nimm()
            if art == "klzu":
                break
            if art is None:
                raise FilterFehler(f"Zeichen {pos + 1}: „)“ fehlt in {name}:(…)")
            if art == "wort" and wert == "OR":
                continue
            if art not in ("wort", "zitat"):
                raise FilterFehler(f"Zeichen {p + 1}: Wert erwartet in {name}:(…)")
            werte.append(_wert(name, wert))
        if not werte:
            raise FilterFehler(f"Zeichen {pos + 1}: {name}:() ist leer")
        return werte[0] if len(werte) == 1 else Knoten("oder", tuple(werte))


def _wert(name: str, roh: str) -> Knoten:
    praefix = roh.endswith("*")
    w = roh[:-1] if praefix else roh
    w = w.strip('"')
    return Knoten("praefix" if praefix else "tag", wert=f"{name}:{w}")


def lies(text: str) -> Knoten:
    """Filtertext → Syntaxbaum. Fehler mit Stelle (FilterFehler)."""
    p = _Parser(text or "")
    if not p.t:
        raise FilterFehler("leerer Filter")
    k = p.ausdruck()
    if p.i < len(p.t):
        raise FilterFehler(f"Zeichen {p.t[p.i][2] + 1}: unerwartet {p.t[p.i][1]!r}")
    return k


def aufloesen(k: Knoten, objekte: dict[str, str], _pfad: tuple = ()) -> Knoten:
    """@Objekte durch ihre (selbst aufgelösten) Filter ersetzen; Zyklen und Unbekanntes benannt."""
    if k.art == "objekt":
        if k.wert in _pfad:
            raise FilterFehler("Objekt-Zyklus: " + " → ".join(_pfad + (k.wert,)))
        if k.wert not in objekte:
            raise FilterFehler(f"Objekt @{k.wert} gibt es nicht")
        return aufloesen(lies(objekte[k.wert]), objekte, _pfad + (k.wert,))
    if k.kinder:
        return Knoten(k.art, tuple(aufloesen(c, objekte, _pfad) for c in k.kinder), k.wert, k.op, k.zahl)
    return k


# ── Übersetzung 1: Regelwerk-Bedingung (im Speicher) ───────────────────────────

def als_bedingung(k: Knoten) -> dict:
    """Syntaxbaum (Objekte aufgelöst) → Bedingung für regelwerk.passt. Freitext ist in
    Regeln nicht erlaubt: Regeln prüfen Hashtags und Felder."""
    if k.art == "und":
        return {"alle": [als_bedingung(c) for c in k.kinder]}
    if k.art == "oder":
        if all(c.art == "tag" for c in k.kinder):
            return {"eines_von": [c.wert for c in k.kinder]}
        return {"irgendeins": [als_bedingung(c) for c in k.kinder]}
    if k.art == "nicht":
        return {"nicht": als_bedingung(k.kinder[0])}
    if k.art == "tag":
        return {"hat": k.wert}
    if k.art == "praefix":
        return {"praefix": k.wert}
    if k.art == "feld":
        z = int(k.zahl) if k.zahl == int(k.zahl) else k.zahl
        if k.op == ">=":
            return {"feld": k.wert, "ab": z}
        if k.op == "<=":
            return {"feld": k.wert, "bis": z}
        if k.op == "=":
            return {"feld": k.wert, "ab": z, "bis": z}
        eps = 1e-9
        return {"feld": k.wert, "ab": z + eps} if k.op == ">" else {"feld": k.wert, "bis": z - eps}
    if k.art == "text":
        raise FilterFehler(f"Freitext „{k.wert}“ ist in Regeln nicht erlaubt — Regeln prüfen Hashtags")
    raise FilterFehler(f"Objekt @{k.wert} nicht aufgelöst")


# ── Übersetzung 2: OpenSearch-Abfrage (Suche, Netzplan, Trefferzahl) ──────────

def als_opensearch(k: Knoten) -> dict:
    """Syntaxbaum (Objekte aufgelöst) → OpenSearch-Query über cs-logs-*."""
    if k.art == "und":
        return {"bool": {"filter": [als_opensearch(c) for c in k.kinder if c.art != "text"],
                         "must": [als_opensearch(c) for c in k.kinder if c.art == "text"]}}
    if k.art == "oder":
        return {"bool": {"should": [als_opensearch(c) for c in k.kinder], "minimum_should_match": 1}}
    if k.art == "nicht":
        return {"bool": {"must_not": [als_opensearch(k.kinder[0])]}}
    if k.art == "tag":
        return {"term": {"hashtags": k.wert}}
    if k.art == "praefix":
        return {"prefix": {"hashtags": k.wert}}
    if k.art == "feld":
        op = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}.get(k.op)
        return ({"term": {k.wert: k.zahl}} if op is None else {"range": {k.wert: {op: k.zahl}}})
    if k.art == "text":
        return {"match_phrase": {"text": k.wert}} if " " in k.wert else {"match": {"text": k.wert}}
    raise FilterFehler(f"Objekt @{k.wert} nicht aufgelöst")


def bedingung(text: str, objekte: dict[str, str] | None = None) -> dict:
    return als_bedingung(aufloesen(lies(text), objekte or {}))


def opensearch(text: str, objekte: dict[str, str] | None = None) -> dict:
    return als_opensearch(aufloesen(lies(text), objekte or {}))
