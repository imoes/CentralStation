"""Wie oft ein Graylog-Fehler wirklich aufgetreten ist — gezählt von Graylog, nicht geschätzt.

Der Feed fasst gleiche Meldungen zusammen (dedup_key, 10-Minuten-Fenster). Das ist
richtig, aber bisher still: ein Eintrag stand da, als sei der Fehler einmal passiert,
obwohl es Tausende waren. Außerdem kamen Fehler nur in den Feed, wenn sie zufällig
unter den 50 neuesten Meldungen eines Laufs waren — bei ~47 000 Meldungen in 10
Minuten fiel ein Dauerfehler mit 85 000 Zeilen in 6 Stunden zweimal in 48 Stunden auf.

Deshalb zählt Graylog selbst: zu jedem Eintrag gibt es eine Zählabfrage (die Abfrage,
aus der die Meldung kam, eingeschränkt auf denselben Host und den festen Teil des
Meldungstextes) und eine Aufteilung nach Container. Jeder Eintrag deckt ein
Zeitfenster [since, until] ab; aufeinanderfolgende Einträge desselben Fehlers
schließen lückenlos und ohne Überlappung aneinander an, damit nichts doppelt oder gar
nicht gezählt wird.

Dieses Modul enthält nur reine Funktionen — die Abfrage an Graylog steht im Connector.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.services.connectors.graylog import strip_timestamps

#: Höchstens so viele Wörter gehen in die Phrase. Länger macht die Abfrage nicht
#: genauer, nur teurer.
MAX_PHRASE_TOKENS = 12

#: Weniger als zwei feste Wörter ("ERROR", "failed") träfen beliebige andere
#: Meldungen desselben Hosts — dann lieber gar nicht zählen als falsch.
MIN_PHRASE_TOKENS = 2

#: So viele Container werden pro Eintrag aufgeführt; der Rest steckt in der Summe.
MAX_CONTAINERS = 50

#: So benennt Graylogs Pivot die Gruppe der Meldungen, die das Feld gar nicht haben —
#: Host-Logs ohne Container. Kein Container, also nicht in die Liste; gezählt sind sie
#: trotzdem, in der Gesamtzahl.
_EMPTY_VALUE = "(Empty Value)"

# Ein Wort ist "veränderlich", wenn es eine Ziffer enthält: Zahlen, Zeiten, IDs,
# IP-Adressen, Hex-Adressen, HTTP-Codes. Genau diese Teile normalisiert auch der
# dedup_key weg — die Phrase besteht aus dem, was zwischen zwei Auftreten gleich bleibt.
_VOLATILE = re.compile(r"\d")
# Wörter ohne Buchstaben oder Ziffern ("-", "|", ":") findet der Standard-Analyzer
# ohnehin nicht; sie zählen nicht als Wort, unterbrechen die Phrase aber auch nicht.
_WORDLIKE = re.compile(r"[A-Za-zÄÖÜäöüß]")


def count_phrase(message: str) -> str | None:
    """Der längste feste Abschnitt einer Meldung, als Suchphrase für Graylog.

    Aus "2026-10-01 10:37:05,393 - ERROR - …ChangelogReader - Failed to read
    change-log: HTTP Status '401 Unauthorized'" wird "- ERROR - …ChangelogReader -
    Failed to read change-log: HTTP Status". None, wenn kein Abschnitt mit mindestens
    MIN_PHRASE_TOKENS Wörtern übrig bleibt.
    """
    first_line = (strip_timestamps(message or "").splitlines() or [""])[0]
    best: list[str] = []
    run: list[str] = []
    for token in first_line.split():
        if _VOLATILE.search(token):
            if _words(run) > _words(best):
                best = run
            run = []
        else:
            run.append(token)
    if _words(run) > _words(best):
        best = run
    # Auf MAX_PHRASE_TOKENS Wörter kürzen, Satzzeichen-Tokens nicht mitgezählt.
    out: list[str] = []
    for token in best:
        out.append(token)
        if _words(out) >= MAX_PHRASE_TOKENS:
            break
    # Führende/abschließende Satzzeichen-Tokens tragen nichts bei.
    while out and not _WORDLIKE.search(out[0]):
        out.pop(0)
    while out and not _WORDLIKE.search(out[-1]):
        out.pop()
    if _words(out) < MIN_PHRASE_TOKENS:
        return None
    return " ".join(out)


def _words(tokens: list[str]) -> int:
    return sum(1 for t in tokens if _WORDLIKE.search(t))


def _quote(value: str) -> str:
    """Lucene-Phrase: innerhalb von Anführungszeichen sind nur \\ und " besonders."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def count_query(base_query: str, source: str, message: str) -> str | None:
    """Die Zählabfrage zu einer Meldung, oder None, wenn sie sich nicht sicher bilden lässt.

    `base_query` ist die Abfrage, aus der die Meldung stammt. Sie bleibt Teil der
    Zählung, damit dieselben Filter gelten wie für den Feed — etwa level:<=4, das die
    zweite, als Info gestempelte Kopie jeder Docker-Zeile ausschließt.
    """
    phrase = count_phrase(message)
    if not phrase or not base_query:
        return None
    parts = [f"({base_query})"]
    if source:
        parts.append(f"source:{_quote(source)}")
    parts.append(f"message:{_quote(phrase)}")
    return " AND ".join(parts)


def window_since(now: datetime, window_minutes: int, previous_until: str | None) -> str:
    """Beginn des Zählfensters für einen neuen Eintrag.

    Normalerweise das Abfragefenster des Laufs (jetzt − window_minutes). Hat derselbe
    Fehler schon einen Vorgänger, der später endet, beginnt das neue Fenster dort —
    sonst würden die Minuten dazwischen in beiden Einträgen gezählt.
    """
    start = now.timestamp() - window_minutes * 60
    prev = parse_ts(previous_until)
    if prev is not None and prev.timestamp() > start:
        start = prev.timestamp()
    return iso(datetime.fromtimestamp(start, tz=timezone.utc))


def iso(dt: datetime) -> str:
    """Zeitstempel im Format, das Graylogs absolute Zeitfenster verlangen."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def containers_from_pivot(rows: list[dict]) -> list[dict]:
    """Graylog-Pivot-Zeilen → [{"name", "count"}], größte zuerst.

    Als Liste, nicht als {name: count}: das metadata-Feld im Feed-Index ist dynamisch
    gemappt, und jeder Containername als Schlüssel wäre ein neues Feld im Mapping.
    """
    out = []
    for row in rows or []:
        key = row.get("key") or []
        if row.get("source") != "leaf" or not key or not key[0] or key[0] == _EMPTY_VALUE:
            continue  # Summenzeile oder Meldungen ohne Container
        value = next((v.get("value") for v in row.get("values") or []
                      if (v.get("key") or [None])[-1] == "count()"), None)
        if isinstance(value, (int, float)):
            out.append({"name": str(key[0]), "count": int(value)})
    out.sort(key=lambda c: (-c["count"], c["name"]))
    return out[:MAX_CONTAINERS]


_PLACEHOLDER = re.compile(r"<[^<>\s]*>")


def template_phrase(template: str) -> str | None:
    """Suchphrase für ein Drain-Muster: Platzhalter (<*>, <NUM>, <IP> …) gelten als
    veränderliche Teile, wie Zahlen in count_phrase."""
    return count_phrase(_PLACEHOLDER.sub("0", template or ""))
