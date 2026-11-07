"""Rückfragen zwischen KI-Instanzen mit Kontext — wie Peer-Sessions (Nutzer 07.11.2026).

Vorher konnten die Agenten der Flotte einander nur über abgeschlossene Analysen erreichen
(lw_gespraech, lw_wissen); ki_lease verhinderte nur, dass zwei denselben Host analysieren. Jetzt:

  lw_frage(host, frage, belege)   adressiert den Agenten, der den Host gerade analysiert (ki_lease).
                                  Analysiert ihn niemand → Status „niemand“ mit Hinweis auf lw_gespraech.
                                  Der Fragende wartet höchstens WARTE_S; danach steht die Antwort unter lw_fragen.
  Zustellung                      vor dem nächsten Modellaufruf des Ziel-Agenten (trichter._schleife) als
                                  Worker-Zug mit Frage und Kontext.
  lw_antwort(id, antwort, belege) nur für Fragen an diesen Agenten; nur belegbare Fakten.
  Ende des Falls                  noch offene Fragen an den Agenten → „unbeantwortet“ mit Grund (nichts still).

Die Konsole fragt über MCP (lw_frage/lw_fragen) mit von = konsole:<Nutzer>; sie selbst nimmt keine Fragen an.
Grenze, benannt: kein Agent wartet auf einen anderen, der selbst wartet — beide fahren nach WARTE_S fort, die
Antwort kommt in der nächsten Runde (kein Verklemmen).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

#: so lange wartet ein Fragender im Werkzeugaufruf auf die Antwort (Agentenbudget 480 s)
WARTE_S = 30
TAKT_S = 3
FRAGE_MAX = 1500
ANTWORT_MAX = 3000
STATUS = ("offen", "zugestellt", "beantwortet", "unbeantwortet", "niemand")


def als_zug(f: dict) -> str:
    """Eine zugestellte Frage als Text für den Ziel-Agenten. Rein — testbar."""
    k = f.get("kontext") or {}
    teile = [f"Rückfrage {f['id']} von {f['von']} zu {f['an_host']}: {f['frage']}"]
    if k.get("fall"):
        teile.append(f"Kontext — Fall des Fragenden: {k['fall']}")
    if k.get("belege"):
        teile.append("Belege des Fragenden: " + ", ".join(map(str, k["belege"][:10])))
    teile.append(f"Beantworte sie mit lw_antwort(id=\"{f['id']}\", antwort, belege) — nur, was du belegen kannst; "
                 "weißt du es nicht, sag das. Danach setze deine Analyse fort.")
    return "\n".join(teile)


def _zeile(r) -> dict:
    return {"id": str(r.id), "von": r.von, "an_host": r.an_host, "an_agent": r.an_agent, "frage": r.frage,
            "kontext": r.kontext or {}, "status": r.status, "grund": r.grund, "antwort": r.antwort,
            "belege": r.belege or [], "gestellt_at": r.gestellt_at.isoformat() if r.gestellt_at else None,
            "beantwortet_at": r.beantwortet_at.isoformat() if r.beantwortet_at else None}


async def stelle(von: str, host: str, frage: str, kontext: dict | None = None, warte_s: float = WARTE_S) -> dict:
    """Frage an den Agenten, der `host` gerade analysiert. Wartet bis warte_s auf die Antwort."""
    import uuid

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiFrage, KiLease
    from app.services.log_watch.hosts import norm
    frage = (frage or "").strip()
    host = (host or "").strip().lower()
    if not frage or not host:
        return {"abgelehnt": "host und frage sind Pflicht"}
    jetzt = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        leases = (await db.execute(select(KiLease).where(KiLease.bis >= jetzt))).scalars().all()
        ziel = next((l for l in leases if l.host.lower() == host or norm(l.host) == norm(host)), None)
        if ziel is not None and ziel.agent == von:
            return {"abgelehnt": f"{host} analysierst du selbst"}
        f = KiFrage(id=uuid.uuid4(), von=von[:200], an_host=ziel.host if ziel else host,
                    an_agent=ziel.agent if ziel else None, frage=frage[:FRAGE_MAX], kontext=kontext or {},
                    status="offen" if ziel else "niemand",
                    grund="" if ziel else "kein Agent analysiert diesen Host gerade",
                    gestellt_at=jetzt)
        db.add(f)
        await db.commit()
        fid = f.id
    if ziel is None:
        return {"id": str(fid), "status": "niemand",
                "hinweis": f"Niemand analysiert {host} gerade. Frühere Analysen: lw_gespraech(\"host:{host}\"), "
                           f"frühere Befunde: lw_wissen(\"host:{host}\")."}
    waited = 0.0
    while waited < warte_s:
        await asyncio.sleep(TAKT_S)
        waited += TAKT_S
        r = await lies(str(fid))
        if r and r["status"] in ("beantwortet", "unbeantwortet"):
            return r
    r = await lies(str(fid)) or {}
    return {**r, "hinweis": f"Noch keine Antwort nach {int(warte_s)} s — sie erscheint unter lw_fragen(id=\"{fid}\")."}


async def lies(fid: str) -> dict | None:
    import uuid

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiFrage
    try:
        u = uuid.UUID(str(fid))
    except ValueError:
        return None
    async with AsyncSessionLocal() as db:
        r = await db.get(KiFrage, u)
        return _zeile(r) if r else None


async def liste(von: str | None = None, host: str | None = None, stunden: float = 24, limit: int = 20) -> list[dict]:
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiFrage
    q = select(KiFrage).where(KiFrage.gestellt_at >= datetime.now(timezone.utc) - timedelta(hours=stunden))
    if von:
        q = q.where(KiFrage.von == von)
    if host:
        q = q.where(KiFrage.an_host == host)
    async with AsyncSessionLocal() as db:
        return [_zeile(r) for r in (await db.execute(q.order_by(KiFrage.gestellt_at.desc()).limit(limit))).scalars()]


async def posteingang(agent: str) -> list[dict]:
    """Offene Fragen an `agent` → zugestellt (einmal). Für trichter._schleife vor jedem Modellaufruf."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiFrage
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(KiFrage).where(KiFrage.an_agent == agent, KiFrage.status == "offen")
                                 .order_by(KiFrage.gestellt_at))).scalars().all()
        for r in rows:
            r.status = "zugestellt"
        await db.commit()
        return [_zeile(r) for r in rows]


async def antworte(agent: str, fid: str, antwort: str, belege: list | None = None) -> dict:
    import uuid

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiFrage
    try:
        u = uuid.UUID(str(fid))
    except ValueError:
        return {"abgelehnt": f"Frage {fid!r} unbekannt"}
    if not (antwort or "").strip():
        return {"abgelehnt": "antwort ist Pflicht (weißt du es nicht, sag das)"}
    async with AsyncSessionLocal() as db:
        r = await db.get(KiFrage, u)
        if r is None:
            return {"abgelehnt": f"Frage {fid} unbekannt"}
        if r.an_agent != agent:
            return {"abgelehnt": f"Frage {fid} ist nicht an dich gerichtet (an {r.an_agent})"}
        if r.status not in ("offen", "zugestellt"):
            return {"abgelehnt": f"Frage {fid} ist schon {r.status}"}
        r.antwort, r.belege = antwort.strip()[:ANTWORT_MAX], list(belege or [])[:20]
        r.status, r.beantwortet_at = "beantwortet", datetime.now(timezone.utc)
        await db.commit()
        return {"beantwortet": str(r.id)}


async def schliesse_offene(agent: str) -> int:
    """Fall zu Ende: noch offene/zugestellte Fragen an den Agenten → unbeantwortet, mit Grund."""
    from sqlalchemy import update

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiFrage
    async with AsyncSessionLocal() as db:
        r = await db.execute(update(KiFrage).where(KiFrage.an_agent == agent,
                                                   KiFrage.status.in_(("offen", "zugestellt")))
                             .values(status="unbeantwortet", grund="Agent hat seinen Fall beendet, ohne zu antworten",
                                     beantwortet_at=datetime.now(timezone.utc)))
        await db.commit()
        return r.rowcount or 0
