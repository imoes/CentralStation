"""Wiederkehrendes Tagesmuster eines Dateisystems: füllt sich, wird bereinigt — kein Alarm, solange es dem Muster folgt.

Nutzer 08.11.2026: „Wir haben in CheckMK einige Hosts, bei denen das Verzeichnis /home/ngrc fast voll läuft und dann
wieder bereinigt wird … Der Agent muss solche Muster erkennen und nur bei einem echten Fehler Alarm melden.“

Gemessen 08.11. (CheckMK, cue0112 Filesystem /home/ngrc, fs_used_percent, 7 Tage): werktags ~20 % um 05:00, bis 14:00
auf 61–69 %, abends bereinigt auf ~25 %; Samstag flach. CheckMK meldet WARN/CRIT nicht am Füllstand
(Grenzen 90,4 %/95,2 %), sondern an der Trendvorhersage: „trend per 1 day: +126 GiB … Time left until disk full:
2 days 19 hours (warn/crit below 3 days/2 days)“ — der Vormittagsanstieg wird auf den Tag hochgerechnet.

Erkannt (alles rein, testbar):
  trend_alarm(output)        WARN/CRIT nur durch den Trend: „Time left until disk full“ und Füllstand unter der
                             eigenen Prozent-Warngrenze
  tagesmuster(reihe, jetzt)  ≥ MIN_TAGE Tage mit Hub ≥ HUB_PP Prozentpunkten, die wieder auf den Grundwert
                             (+ RUECK_PP) zurückfielen
Echter Fehler — das Muster deckt den Alarm NICHT:
  - der Füllstand liegt über dem bisherigen Tageshöchststand (+ UEBER_PP): ungewöhnlicher Anstieg
  - die Bereinigung blieb aus: das Minimum der letzten 24 h liegt über Grundwert + RUECK_PP
  - eine Prozentgrenze ist erreicht (dann ist es kein reiner Trendalarm, trend_alarm → None)
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

HUB_PP = 15.0
RUECK_PP = 10.0
UEBER_PP = 5.0
MIN_TAGE = 3
MIN_PUNKTE_JE_TAG = 8

_TREND = re.compile(r"Time left until (?:disk|filesystem) full", re.I)
_PROZENT = re.compile(r"Used:\s*([\d.]+)%.*?warn/crit at ([\d.]+)%/([\d.]+)% used", re.S)


def ist_dateisystem(service: str) -> bool:
    return (service or "").startswith(("Filesystem ", "Mount "))


def trend_alarm(output: str) -> dict | None:
    """Ist der Alarm allein von der Trendvorhersage ausgelöst? → {used, warn, crit}, sonst None."""
    if not _TREND.search(output or ""):
        return None
    m = _PROZENT.search(output or "")
    if not m:
        return None
    used, warn, crit = (float(x) for x in m.groups())
    return {"used": used, "warn": warn, "crit": crit} if used < warn else None


def tagesmuster(reihe: list[tuple[datetime, float]], jetzt: datetime, used_jetzt: float) -> dict:
    """Füllstand-Reihe (Zeit, Prozent) der letzten Tage → {erkannt, grund, …} mit den Zahlen dazu."""
    je_tag: dict = defaultdict(list)
    for t, v in reihe:
        je_tag[t.date()].append(v)
    tage = {d: (min(v), max(v)) for d, v in je_tag.items() if len(v) >= MIN_PUNKTE_JE_TAG}
    if not tage:
        return {"erkannt": False, "grund": "kein Verlauf aus CheckMK"}
    grundwert = min(lo for lo, _ in tage.values())
    zyklus = {d: (lo, hi) for d, (lo, hi) in tage.items()
              if hi - lo >= HUB_PP and lo <= grundwert + RUECK_PP}
    zahlen = {"tage_mit_zyklus": len(zyklus), "tage_gesamt": len(tage), "grundwert_pct": round(grundwert, 1),
              "tageshoechststand_pct": round(max((hi for _, hi in zyklus.values()), default=0.0), 1),
              "jetzt_pct": round(used_jetzt, 1)}
    if len(zyklus) < MIN_TAGE:
        return {"erkannt": False, **zahlen,
                "grund": f"kein Tagesmuster: nur {len(zyklus)} von {len(tage)} Tagen mit Anstieg ≥ {HUB_PP:g} Pp "
                         f"und Rückgang auf den Grundwert"}
    if used_jetzt > zahlen["tageshoechststand_pct"] + UEBER_PP:
        return {"erkannt": False, **zahlen,
                "grund": f"über dem bisherigen Tageshöchststand: jetzt {used_jetzt:.1f} %, bisher höchstens "
                         f"{zahlen['tageshoechststand_pct']:.1f} %"}
    letzte = [v for t, v in reihe if t >= jetzt - timedelta(hours=24)]
    if letzte and min(letzte) > grundwert + RUECK_PP:
        return {"erkannt": False, **zahlen,
                "grund": f"Bereinigung ausgeblieben: in 24 h nie unter {min(letzte):.1f} % "
                         f"(Grundwert {grundwert:.1f} %)"}
    return {"erkannt": True, **zahlen,
            "grund": f"bekanntes Tagesmuster: an {len(zyklus)} von {len(tage)} Tagen von ~{grundwert:.0f} % auf bis "
                     f"{zahlen['tageshoechststand_pct']:.0f} % und wieder bereinigt; jetzt {used_jetzt:.1f} % — "
                     "Trendalarm, kein Füllstandsalarm"}
