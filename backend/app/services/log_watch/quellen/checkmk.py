"""CheckMK: die „Nachrichten“ sind die aktuellen Service-Probleme (Zustand ≠ OK) mit ihren
Spalten und Labels. Gezählt wird hier im Code — es sind wenige hundert Zeilen
(gemessen 2026-11-06: 298 Probleme bei 96 235 Services).

Spalten werden auf die Namen der anderen Quellen gebracht (`description` → `service`).
Label-Schlüssel, die einen Hostnamen enthalten (`cmk/piggyback_source_<host>`), werden zu
EINEM Feld mit dem Host als Wert — sonst wäre jeder Host ein eigenes Feld.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from app.services.log_watch.catalog import BEISPIELE_JE_FELD, Feld, braucht_werte

#: Livestatus-Spalte → Feldname im Katalog
SPALTEN = {
    "host_name": "host_name",
    "description": "service",
    "state": "state",
    "state_type": "state_type",
    "host_state": "host_state",
    "plugin_output": "plugin_output",
    "long_plugin_output": "long_plugin_output",
    "check_command": "check_command",
    "host_groups": "host_groups",
    "groups": "service_groups",
    "acknowledged": "acknowledged",
    "scheduled_downtime_depth": "in_downtime",
    "current_attempt": "current_attempt",
}
_LABEL_MIT_HOST = re.compile(r"^(cmk/piggyback_source)_(.+)$")


def label_feld(schluessel: str, wert) -> tuple[str, object]:
    """Label → (Feldname, Wert); ein Hostname im Schlüssel wird zum Wert."""
    m = _LABEL_MIT_HOST.match(schluessel)
    if m:
        return f"label:{m.group(1)}", m.group(2)
    return f"label:{schluessel}", wert


def zeilen_aus_api(value: list[dict]) -> list[dict]:
    """REST-Antwort (`/domain-types/service/collections/all`) → [{feld: wert}]."""
    zeilen = []
    for x in value:
        e = x.get("extensions") or {}
        z = {feld: e[spalte] for spalte, feld in SPALTEN.items() if spalte in e}
        if "in_downtime" in z:
            z["in_downtime"] = bool(z["in_downtime"])
        for quelle in ("host_labels", "labels"):
            for k, v in (e.get(quelle) or {}).items():
                name, wert = label_feld(k, v)
                z[name] = wert
        zeilen.append(z)
    return zeilen


def _leer(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _typ(werte: list) -> tuple[str, bool]:
    if any(isinstance(v, list) for v in werte):
        return "liste", False
    if werte and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in werte):
        return "zahl", True
    if werte and all(isinstance(v, bool) for v in werte):
        return "bool", False
    return "text", False


def felder_aus_zeilen(zeilen: list[dict]) -> list[Feld]:
    namen = sorted({k for z in zeilen for k in z} | set(SPALTEN.values()))
    out = []
    for n in namen:
        typ, num = _typ([z[n] for z in zeilen if not _leer(z.get(n))])
        out.append(Feld(name=n, typ=typ, numerisch=num))
    return out


def messe_zeilen(zeilen: list[dict], felder: list[Feld]) -> dict[str, dict]:
    """Je Feld Abdeckung, Kardinalität und (für Kandidaten) Beispielwerte; Listen zählen je Element."""
    stats: dict[str, dict] = {}
    for f in felder:
        zaehl: Counter = Counter()
        mit = 0
        for z in zeilen:
            v = z.get(f.name)
            if _leer(v):
                continue
            mit += 1
            for w in (v if isinstance(v, list) else [v]):
                zaehl[json.dumps(w, ensure_ascii=False) if isinstance(w, (dict, list)) else str(w)] += 1
        stats[f.name] = {"mit_feld": mit, "kardinalitaet": len(zaehl)}
        if braucht_werte(f, stats[f.name]):
            stats[f.name]["beispielwerte"] = [{"wert": w[:200], "anzahl": n}
                                              for w, n in zaehl.most_common(BEISPIELE_JE_FELD)]
    return stats


class CheckMKQuelle:
    name = "checkmk"
    fenster = "aktuelle Service-Probleme"

    def __init__(self, k):
        self.k = k
        self._zeilen: list[dict] | None = None

    async def _probleme(self) -> list[dict]:
        if self._zeilen is None:
            r = await self.k._request(
                "GET", "/domain-types/service/collections/all",
                params={"columns": list(SPALTEN) + ["labels", "host_labels"],
                        "query": json.dumps({"op": "!=", "left": "state", "right": "0"})})
            r.raise_for_status()
            self._zeilen = zeilen_aus_api(r.json().get("value") or [])
        return self._zeilen

    async def felder(self) -> list[Feld]:
        return felder_aus_zeilen(await self._probleme())

    async def messe(self, felder: list[Feld]) -> tuple[int, dict[str, dict]]:
        zeilen = await self._probleme()
        return len(zeilen), messe_zeilen(zeilen, felder)
