"""Wer bekommt ein neu gebautes KI-Lagebild — und wer nicht.

Der Hintergrund-Job baute vorher JEDES existierende Lagebild neu, alle 15 Minuten,
Tag und Nacht. Bei fünf Nutzern — zwei davon seit über 85 Tagen nicht angemeldet —
brauchte ein Durchlauf länger als das Intervall: die Läufe überholten sich und das
Modell kam nie zur Ruhe. Die Regel, die das begrenzt, wird hier festgehalten, weil
ihr Versagen nicht auffällt (es entstehen nur still Kosten).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.ai_agent.scheduler import _viewed_within


def _dash(meta):
    return SimpleNamespace(generation_meta=meta)


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def test_viewed_today_is_active():
    assert _viewed_within(_dash({"last_viewed_at": _ago(hours=2)}), 7) is True


def test_viewed_just_inside_the_window():
    assert _viewed_within(_dash({"last_viewed_at": _ago(days=6, hours=23)}), 7) is True


def test_viewed_outside_the_window_is_inactive():
    assert _viewed_within(_dash({"last_viewed_at": _ago(days=8)}), 7) is False


def test_long_dormant_account_is_inactive():
    """Der reale Fall: zwei Konten mit 85 und 102 Tagen ohne Anmeldung."""
    assert _viewed_within(_dash({"last_viewed_at": _ago(days=85)}), 7) is False


@pytest.mark.parametrize("meta", [None, {}, {"last_viewed_at": None},
                                  {"last_viewed_at": ""}, {"last_viewed_at": "kaputt"}])
def test_unknown_counts_as_inactive(meta):
    """Ohne verwertbaren Zeitstempel wird NICHT gearbeitet.

    Im Zweifel nichts tun: ein unnötiger LLM-Aufruf kostet, ein ausgelassener nicht.
    Das gilt auch für alle Lagebilder, die es vor dieser Änderung schon gab — sie
    tragen den Stempel noch nicht und ruhen, bis jemand die Ansicht öffnet.
    """
    assert _viewed_within(_dash(meta), 7) is False


def test_naive_timestamp_is_read_as_utc():
    """Ein Zeitstempel ohne Zeitzone darf nicht zu einem Absturz führen."""
    naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    assert _viewed_within(_dash({"last_viewed_at": naive.isoformat()}), 7) is True


def test_zero_days_stops_everything():
    """Fenster 0 hält alles an — jeder Zeitstempel liegt in der Vergangenheit.

    Das ist die brauchbare Bedeutung: 0 wirkt wie ein zweiter Ausschalter, statt
    ein unbestimmtes Verhalten zu erzeugen.
    """
    assert _viewed_within(_dash({"last_viewed_at": _ago(seconds=1)}), 0) is False
    assert _viewed_within(_dash({"last_viewed_at": _ago(hours=1)}), 0) is False


def test_negative_days_does_not_invert():
    """Ein negativer Wert darf das Fenster nicht in die Zukunft drehen."""
    assert _viewed_within(_dash({"last_viewed_at": _ago(seconds=1)}), -5) is False
