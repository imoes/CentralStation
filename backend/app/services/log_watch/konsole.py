"""Konsolen-Gedächtnis: Konsolen-Sitzungen mit Bezug werden Teil des Gesprächsprotokolls.

Nur Sitzungen mit Bezug (Nutzer, 2026-11-07): ein Incident (`incident:<uuid>`), ein Alert
(`external_id`) oder ein Ticket. Eine freie Konsolen-Sitzung ohne Bezug bleibt im Container
und wird nicht gespeichert.

Quelle ist der Verlauf, den auch die Konsole anzeigt (`/sessions/{sid}/history` im
Nutzer-Container) — kein Mitschnitt des SSE-Stroms, also kein zweiter Weg zum selben Text.
Gespeichert wird nach jeder Antwort die ganze Sitzung; die Dokument-ID `sitzung:zug` macht
das Überschreiben idempotent.

Gefunden wird eine Sitzung wie jedes Gespräch über ihre Hashtags: `quelle:konsole`, der
Bezug (`incident:…`, `alert:…`, `ticket:…`) und `host:` je betroffenem Host. So liest der
Trichter im Host-Objekt (gedaechtnis) auch, was Menschen in der Konsole dazu geklärt haben.
Werkzeugausgaben der Konsole werden nicht übernommen — nur was Mensch und KI geschrieben haben.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

HERKUNFT = "quelle:konsole"
#: Rollen des Konsolen-Verlaufs → Rollen des Protokolls; alles andere (tool, system) entfällt
ROLLEN = {"user": "mensch", "assistant": "ki"}


def bezug(external_id: str | None, ticket_key: str | None) -> list[str]:
    """Bezugs-Hashtags einer Sitzung; leer heißt: kein Bezug, nicht speichern."""
    tags = []
    if external_id:
        tags.append(external_id if external_id.startswith("incident:") else f"alert:{external_id}")
    if ticket_key:
        tags.append(f"ticket:{ticket_key}")
    return tags


def _text(inhalt) -> str:
    if isinstance(inhalt, list):
        return "\n".join(b.get("text", "") for b in inhalt if isinstance(b, dict) and b.get("type", "text") == "text")
    return str(inhalt or "")


def verlauf_aus_historie(historie: list[dict]) -> list[dict]:
    """Konsolen-Verlauf ([{role, content}]) → Protokoll-Verlauf; leere Züge und Werkzeugzüge entfallen."""
    out = []
    for m in historie or []:
        rolle = ROLLEN.get(m.get("role"))
        text = _text(m.get("content")).strip()
        if rolle and text:
            out.append({"rolle": rolle, "text": text})
    return out


async def hosts_zum_bezug(db, external_id: str | None, label: str | None) -> list[str]:
    """Kanonische Hostnamen (wie `host:` in der Historie): beim Incident Haupt-Host und die Hosts
    seines Fehler-Clusters; sonst der Sitzungsname — aber nur, wenn das Hostverzeichnis ihn kennt."""
    from app.services.error_clusters.incident import cluster_context
    from app.services.log_watch.hosts import verzeichnis

    verz = await verzeichnis(db)
    roh: list[str] = []
    if external_id and external_id.startswith("incident:"):
        import uuid as _uuid

        from app.models.workflow import Incident
        try:
            inc = await db.get(Incident, _uuid.UUID(external_id.split(":", 1)[1]))
        except ValueError:
            inc = None
        if inc is not None:
            roh = [inc.primary_host, *((cluster_context(inc) or {}).get("hosts") or [])]
    elif label:
        e, _k, _grund = verz.zuordnen(label)
        roh = [e.name] if e else []
    hosts = []
    for h in roh:
        e, k, _grund = verz.zuordnen(h or "")
        kanon = e.name if e else k
        if kanon and kanon not in hosts:
            hosts.append(kanon)
    return hosts


async def _bezug_der_sitzung(sid: str, user_id) -> tuple[list[str], list[str]]:
    """(Bezugs-Hashtags, Hosts) der Konsolen-Sitzung; ([], []) ohne Sitzung oder ohne Bezug."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.workflow import ComputerSession

    async with AsyncSessionLocal() as db:
        s = (await db.execute(select(ComputerSession).where(
            ComputerSession.id == sid, ComputerSession.user_id == user_id))).scalar_one_or_none()
        tags = bezug(s.external_id, s.ticket_key) if s is not None else []
        if not tags:
            return [], []
        return tags, await hosts_zum_bezug(db, s.external_id, s.label)


async def sichere(sid: str, user_id, holer: Callable[[], Awaitable[list[dict]]], *, jetzt: datetime | None = None) -> int:
    """Eine Konsolen-Sitzung ins Protokoll schreiben (nur mit Bezug). Rückgabe: geschriebene Züge.
    `zeit` ist der Stand der Sitzung beim Schreiben — der Konsolen-Verlauf kennt keine Zugzeiten."""
    from app.services.log_watch import gespraech

    tags, hosts = await _bezug_der_sitzung(sid, user_id)
    if not tags:
        return 0
    verlauf = verlauf_aus_historie(await holer())
    if not verlauf:
        return 0
    zuege = gespraech.zuege(sid, {"start": tags[0], "hosts": hosts}, [HERKUNFT, *tags], verlauf,
                            jetzt or datetime.now(timezone.utc))
    return await gespraech.schreibe(zuege)
