"""Mindestalter: gemeldet wird erst, was 30 min besteht (rules.min_age_minutes).

Anlass 10/2026: die meisten Sofort-Mails kamen von Ausfällen, die nach 0–15 min von selbst
vorbei waren (Neustarts, nächtliche Jobs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services.error_clusters import runner
from app.services.error_clusters.rules import DEFAULT

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@dataclass
class _Ep:
    id: str
    key: str
    started_at: datetime
    last_active_at: datetime
    closed_at: datetime | None = None


@dataclass
class _Ev:
    action: str
    episode: _Ep
    mail: bool = True


@dataclass
class _Store:
    open: dict = field(default_factory=dict)


@dataclass
class _Rt:
    store: _Store


def _setup(started_min_ago: int):
    runner._held.clear()
    ep = _Ep("e1", "k1", NOW - timedelta(minutes=started_min_ago), NOW)
    return _Rt(_Store({"k1": ep})), ep


def test_default_is_thirty_minutes():
    assert DEFAULT.min_age_minutes == 30


def test_young_outbreak_mail_is_held_not_sent():
    rt, ep = _setup(5)
    out = runner._hold_young(rt, [_Ev("open", ep)], NOW)
    assert out[0].mail is False and "e1" in runner._held


def test_held_mail_goes_out_when_old_enough_and_still_active():
    rt, ep = _setup(5)
    runner._hold_young(rt, [_Ev("open", ep)], NOW)
    later = NOW + timedelta(minutes=26)
    ep.last_active_at = later - timedelta(minutes=5)
    due = runner._release_held(rt, later)
    assert len(due) == 1 and due[0].mail is True and not runner._held


def test_held_mail_is_dropped_when_the_outbreak_ended_early():
    """Neustart: 10 min rot, dann vorbei — keine Mail."""
    rt, ep = _setup(5)
    runner._hold_young(rt, [_Ev("open", ep)], NOW)
    ep.closed_at = NOW + timedelta(minutes=10)
    rt.store.open.clear()
    assert runner._release_held(rt, NOW + timedelta(minutes=30)) == [] and not runner._held


def test_held_mail_is_dropped_when_no_longer_active():
    rt, ep = _setup(5)
    runner._hold_young(rt, [_Ev("open", ep)], NOW)
    ep.last_active_at = NOW                      # seit 30 min nichts mehr
    assert runner._release_held(rt, NOW + timedelta(minutes=30)) == []


def test_old_outbreaks_and_renotify_are_not_held():
    rt, ep = _setup(45)
    assert runner._hold_young(rt, [_Ev("open", ep)], NOW)[0].mail is True
    rt, ep = _setup(5)
    assert runner._hold_young(rt, [_Ev("renotify", ep)], NOW)[0].mail is True
    assert not runner._held
