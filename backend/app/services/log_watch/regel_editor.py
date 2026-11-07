"""Regel-Editor wie auf einer Palo-Alto-Firewall: Treffer je Regel und die Vorschau eines Entwurfs.

Nutzer 07.11.2026: „ich habe keinen Regeleditor wie bei einer FW gesehen“. Die Daten dazu gibt es:
jeder Rasterlauf zählt je Regel, wie oft sie entschied (`je_regel`), wie oft sie passte, aber eine frühere
Regel entschied (`regel_verdraengt` — die gemessene Verdeckung), und wie oft eine Regel im Testmodus
gepasst hätte (`regel_test`). Incident-Regeln zählen ihre Kandidaten im Lauf „incident-regeln“.

Die Vorschau prüft einen Entwurf gegen die gespeicherten Nachrichten der Historie: welche Urteile
sich ändern würden. Grenze, benannt: gespeichert ist nur, was relevant war — Nachrichten, die heute
verworfen werden, kann die Vorschau nicht nachträglich hereinholen.
"""
from __future__ import annotations

from collections import Counter

#: Läufe, deren je_regel Urteile je Nachricht zählt (nicht: Zusammenfassungen, KI, Stufe 0)
URTEIL_QUELLEN = ("graylog", "wazuh", "checkmk", "firewall", "switch")
VORSCHAU_MAX = 3000
BEISPIELE = 8


def treffer_je_regel(zeilen: list[tuple[str, object, dict]]) -> dict[str, dict]:
    """(quelle, fenster_start, werte) der Läufe → je Regel {treffer, verdraengt, test, letzter}. Rein."""
    treffer, verdraengt, test = Counter(), Counter(), Counter()
    letzter: dict[str, object] = {}
    for quelle, b, w in zeilen:
        if not isinstance(w, dict) or "fehler" in w:
            continue
        if quelle in URTEIL_QUELLEN:
            quellen = (("je_regel", treffer), ("regel_verdraengt", verdraengt), ("regel_test", test))
        elif quelle == "incident-regeln":
            quellen = (("je_regel", treffer),)
        else:
            continue
        for schluessel, zaehler in quellen:
            for rid, n in (w.get(schluessel) or {}).items():
                if n:
                    zaehler[rid] += n
                    if schluessel == "je_regel" and (rid not in letzter or b > letzter[rid]):
                        letzter[rid] = b
    ids = set(treffer) | set(verdraengt) | set(test)
    return {rid: {"treffer": treffer[rid], "verdraengt": verdraengt[rid], "test": test[rid],
                  "letzter": letzter.get(rid)} for rid in sorted(ids)}


def vorschau(alt: list, neu: list, docs: list[dict]) -> dict:
    """Urteile der Historie unter dem alten und dem neuen Regelsatz. Nur Dokumente, die das Regelwerk
    allein beurteilt hat (herkunft regel) — KI und Mensch haben Vorrang und ändern sich nicht. Rein."""
    from app.services.log_watch.regelwerk import bewerte
    geaendert, beispiele, test = Counter(), [], Counter()
    geprueft = 0
    for d in docs:
        if (d.get("herkunft") or "regel") != "regel":
            continue
        geprueft += 1
        tags = set(d.get("hashtags") or [])
        felder = {k: d[k] for k in ("level", "relevanz") if d.get(k) is not None}
        a, b = bewerte(alt, tags, felder), bewerte(neu, tags, felder)
        test.update(b.test_treffer)                    # „hätte N Treffer“ der Regeln im Testmodus
        if (a.aktion, a.regel) == (b.aktion, b.regel):
            continue
        geaendert[f"{a.regel} → {b.regel}"] += 1
        if len(beispiele) < BEISPIELE:
            beispiele.append({"id": d.get("_id"), "text": (d.get("text") or "")[:160], "host": d.get("host"),
                              "vorher": {"aktion": a.aktion, "regel": a.regel},
                              "nachher": {"aktion": b.aktion, "regel": b.regel}})
    return {"geprueft": geprueft, "geaendert": sum(geaendert.values()),
            "je_wechsel": dict(geaendert.most_common()), "beispiele": beispiele,
            "test_treffer": dict(test.most_common()),
            "grenze": "nur gespeicherte (relevante) Nachrichten — heute verworfene kann die Vorschau nicht zeigen"}
