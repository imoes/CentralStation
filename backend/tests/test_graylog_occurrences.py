"""Zählung der Graylog-Einträge im Feed: Phrase, Abfrage, Zeitfenster, Container.

Fehler hier fallen nicht auf, sie zählen nur falsch: eine Phrase mit einer Zahl darin
trifft das nächste Auftreten nicht mehr (zu niedrig), eine Phrase aus einem Wort trifft
fremde Meldungen (zu hoch), überlappende Fenster zählen doppelt.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.graylog_occurrences import (
    containers_from_pivot,
    count_phrase,
    count_query,
    iso,
    window_since,
)

ZIPLINE = ("2026-10-01 10:37:05,393 - ERROR - cue.zipline.content_store.changelog_reader."
           "ChangelogReader - Failed to read change-log: HTTP Status '401 Unauthorized'")
LEVEL4 = "level:<=4 AND NOT message:uprobes"


def test_phrase_of_the_reported_message():
    phrase = count_phrase(ZIPLINE)
    assert phrase is not None
    assert "Failed to read change-log: HTTP Status" in phrase
    assert "401" not in phrase and "10:37" not in phrase


def test_phrase_is_the_same_for_every_occurrence():
    """Zwei Auftreten desselben Fehlers zu anderer Zeit müssen dieselbe Phrase ergeben."""
    other = ZIPLINE.replace("10:37:05,393", "08:01:59,004")
    assert count_phrase(other) == count_phrase(ZIPLINE)


def test_phrase_skips_volatile_tokens():
    msg = "connection to 10.0.1.5:5432 refused after 3 retries for user app_reader"
    phrase = count_phrase(msg)
    assert phrase is not None
    assert not any(ch.isdigit() for ch in phrase)


def test_no_phrase_when_nothing_stable_remains():
    assert count_phrase("12:00 503 4711 0x1f") is None
    assert count_phrase("failed 42") is None          # ein Wort reicht nicht
    assert count_phrase("") is None


def test_phrase_uses_only_the_first_line():
    """Mehrzeilige Meldungen (Stacktraces) — die erste Zeile trägt den Fehler."""
    msg = "Unclosed client session\nclient_session: <aiohttp.ClientSession object at 0x7f>"
    assert count_phrase(msg) == "Unclosed client session"


def test_phrase_is_capped():
    msg = " ".join(f"wort{chr(97 + i % 26)}" for i in range(40)).replace("wort", "w")
    assert len((count_phrase(msg) or "").split()) <= 12


def test_count_query_keeps_the_feed_filter_and_host():
    q = count_query(LEVEL4, "cueservices.example", ZIPLINE)
    assert q is not None
    assert q.startswith(f"({LEVEL4}) AND ")
    assert 'source:"cueservices.example"' in q
    assert 'message:"' in q


def test_count_query_escapes_quotes():
    q = count_query(LEVEL4, "h", 'login "admin" failed for realm intern')
    assert q is not None
    assert '\\"admin\\"' in q


def test_count_query_none_without_phrase_or_base():
    assert count_query(LEVEL4, "h", "503 12:00") is None
    assert count_query("", "h", ZIPLINE) is None


NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_window_starts_at_query_window_without_predecessor():
    assert window_since(NOW, 10, None) == "2026-10-01T11:50:00.000Z"


def test_window_continues_from_a_later_predecessor():
    """Endet der Vorgänger nach dem Fensterbeginn, beginnt das neue Fenster dort —
    sonst zählen die Minuten dazwischen doppelt."""
    assert window_since(NOW, 10, "2026-10-01T11:55:30.000Z") == "2026-10-01T11:55:30.000Z"


def test_window_ignores_an_old_predecessor():
    assert window_since(NOW, 10, "2026-10-01T09:00:00.000Z") == "2026-10-01T11:50:00.000Z"


def test_iso_format_for_graylog():
    assert iso(NOW) == "2026-10-01T12:00:00.000Z"


def test_containers_from_pivot_drops_rollup_and_sorts():
    rows = [
        {"key": ["zipline-b"], "values": [{"key": ["count()"], "value": 5}], "source": "leaf"},
        {"key": ["zipline-a"], "values": [{"key": ["count()"], "value": 9}], "source": "leaf"},
        {"key": [], "values": [{"key": ["count()"], "value": 14}], "source": "non-leaf"},
    ]
    assert containers_from_pivot(rows) == [
        {"name": "zipline-a", "count": 9},
        {"name": "zipline-b", "count": 5},
    ]


def test_containers_from_pivot_tolerates_garbage():
    assert containers_from_pivot([]) == []
    assert containers_from_pivot([{"key": [""], "values": [], "source": "leaf"}]) == []


def test_containers_from_pivot_skips_graylogs_empty_value_bucket():
    """Meldungen ohne container_name gruppiert Graylog als "(Empty Value)" — das ist
    kein Container und darf nicht als einer angezeigt werden."""
    rows = [{"key": ["(Empty Value)"], "values": [{"key": ["count()"], "value": 52}],
             "source": "leaf"}]
    assert containers_from_pivot(rows) == []


# ── Dauerfehler im selben Eintrag fortschreiben (12.11.2026: 17 842 von 18 853 Einträgen waren Kopien) ─────────

def test_fortschreiben_nur_offene_eintraege():
    from app.services.graylog_occurrences import fortschreibbar
    assert fortschreibbar("new") and fortschreibbar("acknowledged") and fortschreibbar(None)
    assert not fortschreibbar("resolved")                      # geschlossen → der Fehler kommt wieder: neuer Eintrag


def test_fortgeschrieben_summe_fenster_zuerst_zuletzt():
    from app.services.graylog_occurrences import fortgeschrieben
    o = {"query": "q", "since": "2026-11-12T09:38:00Z", "until": "2026-11-12T10:42:00Z", "count": 660}
    n = fortgeschrieben(o, {"count": 385, "containers": []}, "2026-11-12T11:20:00Z")
    assert (n["since"], n["until"], n["count"], n["gesamt"], n["fenster"]) == (
        "2026-11-12T10:42:00Z", "2026-11-12T11:20:00Z", 385, 1045, 2)
    assert n["erstmals"] == "2026-11-12T09:38:00Z" and n["zuletzt"] == "2026-11-12T11:20:00Z" and n["query"] == "q"
    n2 = fortgeschrieben(n, {"count": 10}, "2026-11-12T12:20:00Z")
    assert (n2["gesamt"], n2["fenster"], n2["erstmals"]) == (1055, 3, "2026-11-12T09:38:00Z")


def test_abgelaufen_neu_angelegt_oder_fortgeschrieben():
    from app.services.graylog_occurrences import abgelaufen
    von, bis = "2026-11-12T09:20:00Z", "2026-11-12T10:20:00Z"
    assert abgelaufen({}, "2026-11-12T09:30:00Z", von, bis)                                  # im Fenster angelegt
    assert abgelaufen({"until": "2026-11-12T10:00:00Z"}, "2026-10-19T10:18:00Z", von, bis)  # alt, aber fortgeschrieben
    assert not abgelaufen({"until": "2026-11-12T08:00:00Z"}, "2026-10-19T10:18:00Z", von, bis)  # Fortschreibung endete
    assert not abgelaufen({}, "2026-11-12T10:30:00Z", von, bis)                              # Fenster noch offen
