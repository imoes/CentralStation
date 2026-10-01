"""Mustererkennung mit Drain3: Masken, stabile Schlüssel, Wiederanlauf, Zählweise."""
from __future__ import annotations

from collections import Counter

import pytest

drain3 = pytest.importorskip("drain3")

from app.services.connectors.graylog import make_dedup_key  # noqa: E402
from app.services.error_clusters.miner import Miner, normalize, template_key  # noqa: E402

LINE_401 = ("2026-10-01 10:37:05,393 - ERROR - cue.zipline.content_store.changelog_reader."
            "ChangelogReader - Failed to read change-log: HTTP Status '401 Unauthorized'")


def _at(ts: str) -> str:
    return LINE_401.replace("10:37:05,393", ts)


def test_same_error_at_other_times_is_one_pattern():
    m = Miner()
    keys = {m.add(_at(t)).key for t in ("08:00:01,001", "08:10:59,999", "11:30:00,000")}
    assert len(keys) == 1


def test_http_status_codes_stay_distinct():
    m = Miner()
    a = m.add(LINE_401)
    b = m.add(LINE_401.replace("401 Unauthorized", "500 Internal Server Error"))
    assert a.key != b.key
    assert "HTTP401" in a.template


def test_numbers_ips_and_ids_are_masked():
    line = "connection 4711 to 10.0.1.5:5432 user 0x7f3a failed, req 3fa85f64-5717-4562-b3fc-2c963f66afa6"
    out = Miner().add(line).template
    for raw in ("4711", "10.0.1.5", "0x7f3a", "3fa85f64"):
        assert raw not in out


def test_only_first_line_counts():
    assert normalize("Unclosed client session\nclient_session: <obj at 0x7f>") == "Unclosed client session"


def test_state_survives_a_reload():
    m = Miner()
    key = m.add(LINE_401).key
    m2 = Miner(m.state())
    assert m2.match(_at("09:00:00,000")).key == key


def test_key_survives_state_loss():
    """Ohne Zustand vergibt Drain3 neue IDs — der Schlüssel ist trotzdem derselbe,
    sonst wäre nach einem Neustart jedes bekannte Muster scheinbar "neu"."""
    a = Miner().add(LINE_401)
    b = Miner().add(LINE_401)
    assert a.key == b.key == template_key(a.template)


def test_ten_thousand_identical_lines_count_ten_thousand():
    """Gegenüberstellung zum Feed-Pfad: dort bleibt pro dedup_key EINE Meldung.
    Die Cluster-Erkennung zählt jede Zeile."""
    m = Miner()
    lines = [_at(f"08:{i // 600 % 60:02d}:{i // 10 % 60:02d},{i % 1000:03d}") for i in range(10_000)]
    tally = Counter(m.add(line).key for line in lines)
    assert sum(tally.values()) == 10_000
    assert len(tally) == 1
    # Feed-Pfad: derselbe Inhalt ergibt einen einzigen Schlüssel → 1 Eintrag
    assert len({make_dedup_key("cueservices", line) for line in lines}) == 1


def test_empty_lines_are_ignored():
    assert Miner().add("   ") is None


def test_key_stays_when_drain_generalizes_the_template():
    """Drain3 macht aus "job alpha failed" + "job beta failed" das Muster "job <*> failed".
    Der Schlüssel bleibt der erste — sonst gälte das Muster mitten im Ausbruch als neu."""
    m = Miner()
    first = m.add("scheduler job alpha failed on retry")
    second = m.add("scheduler job beta failed on retry")
    assert "<*>" in second.template
    assert second.key == first.key
    m2 = Miner(m.state())
    assert m2.add("scheduler job gamma failed on retry").key == first.key
