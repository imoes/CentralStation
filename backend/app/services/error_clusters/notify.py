"""Meldungen der Fehler-Cluster-Erkennung: KI-Einschätzung und Mail (Blöcke 4 und 5).

Bis dahin ein Haken ohne Wirkung — der Runner ruft ihn bereits auf, damit die
Reihenfolge (erst Feed + Incident festschreiben, dann melden) feststeht.
"""
from __future__ import annotations


async def on_events(events, rt, cfg, base_url: str) -> None:
    return None
