"""Switches (nsa/nss/nsc) im Raster: eigener Musterlerner, dieselben Regeln und Hashtags.

Gemessen 06.11.: ca. 241 000 Switch-Meldungen/h, davon Level ≤ 4 ca. 16 700/h (≈ 1 400 je
5 min). Die Fehler-Cluster-Erkennung schließt Switches bewusst aus; hier bekommen sie einen
EIGENEN Drain-Lerner (Zustand in `netz_zustand`), damit Switch-Rauschen die belegte
Fehler-Erkennung nicht verändert.

„Neu“ gilt erst nach einer Lernphase (LERNPHASE ab Start): vorher wäre jedes Muster neu.
Die Lernphase steht als Grund im Urteil — sie verschwindet nicht still.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.services.log_watch.definition import Definition
from app.services.log_watch.hosts import Hostverzeichnis
from app.services.log_watch.vokabular import THEMEN, THEMEN_SWITCH, themen_switch

THEMEN_ALLE = THEMEN + THEMEN_SWITCH

QUERY = "source:(nsa* OR nss* OR nsc*) AND level:<=4"
FELDER = ["message", "source", "level", "timestamp", "facility", "application_name"]
LERNPHASE = timedelta(hours=24)
#: Syslog-Level, ab dem ein Switch einen kritischen Zustand meldet (emerg, alert, crit)
KRITISCH_BIS = 2


def ohne_geraetename(text: str, quelle: str) -> str:
    """Führenden Gerätenamen entfernen („nsa429 fpc1 … Link 7 DOWN“ → „fpc1 … Link 7 DOWN“).

    Sonst lernte der Lerner je Switch ein eigenes Muster, und eine Störung über viele
    Switches sähe aus wie viele Einzelfälle (gemessen 06.11.: Muster mit nsa340, nsa386 …).
    """
    t = text.lstrip()
    kurz = (quelle or "").split(".")[0].lower()
    erstes = t.split(" ", 1)[0].lower().rstrip(":")
    if kurz and erstes in (kurz, (quelle or "").lower()):
        return t.split(" ", 1)[1] if " " in t else ""
    return t


def ist_neu(key: str, bekannt: set[str], gestartet: datetime, fenster: datetime) -> bool:
    """Neues Muster — aber nie in der Lernphase."""
    return key not in bekannt and fenster - gestartet >= LERNPHASE


def gruppen(zeilen: list[dict], lerne) -> list[dict]:
    """Zeilen → Gruppen je (Muster, Switch, Themen der Zeile). `lerne(text, quelle)` →
    (key, template) oder None.

    Die Themen gehören zum Schlüssel: Drain fasst „Link 7 UP“ und „Link 7 DOWN“ zu „Link <NUM>
    <*>“ zusammen (gemessen 06.11.) — aus Muster und erstem Beispiel bestimmt, ginge das
    Thema einer DOWN-Zeile verloren, wenn das Beispiel UP lautet.
    """
    g: dict[tuple, dict] = {}
    for m in zeilen:
        text = str(m.get("message") or "")
        r = lerne(text, str(m.get("source") or ""))
        if r is None:
            continue
        key, template = r
        sw = str(m.get("source") or "")
        th = tuple(t.id for t in themen_switch(text))
        x = g.get((key, sw, th))
        if x is None:
            x = g[(key, sw, th)] = {"key": key, "template": template, "switch": sw, "anzahl": 0,
                                    "themen": list(th), "level": 7, "beispiel": text[:500], "belege": []}
        x["anzahl"] += 1
        try:
            x["level"] = min(x["level"], int(m.get("level")))
        except (TypeError, ValueError):
            pass
        if m.get("_id") and len(x["belege"]) < 5:
            x["belege"].append((m["_id"], m.get("timestamp") or ""))
    return list(g.values())


def bewerte_switch(gruppen_: list[dict], bekannt: set[str], gestartet: datetime, fenster: datetime,
                   verz: Hostverzeichnis, d: Definition) -> list:
    from app.services.log_watch.raster import THEMEN_R5, _host_tags, _sig, _urteil

    out = []
    lernphase = fenster - gestartet < LERNPHASE
    for x in gruppen_:
        signale: set[str] = set()
        gruende = [f"Switch-Meldung (Level {x['level']}): {x['template'][:200]}"]
        th = [t for t in THEMEN_ALLE if t.id in x["themen"]]
        if ist_neu(x["key"], bekannt, gestartet, fenster):
            signale.add("neu")
            gruende.append("neues Muster auf den Switches")
        elif lernphase and x["key"] not in bekannt:
            gruende.append(f"Lernphase bis {(gestartet + LERNPHASE):%d.%m. %H:%M} — noch nicht als neu gewertet")
        for t in th:
            if t.mindest >= 2 or t.id in THEMEN_R5:
                signale.add("schaden")
                gruende.append(f"Thema „{t.text}“ (Schaden an Ressourcen)")
        zustand = []
        if x["level"] <= KRITISCH_BIS:
            zustand = ["zustand:crit"]
            signale.add("dringend")
            gruende.append(f"Switch meldet kritischen Zustand (Syslog-Level {x['level']})")
        kanon, e, htags = _host_tags(x["switch"], verz)
        tags = (["quelle:switch", f"switch:{x['switch'].split('.')[0].lower()}", f"muster:{x['key']}",
                 f"level:{x['level']}"] + zustand + htags + [f"thema:{t.id}" for t in th] + _sig(signale))
        out.append(_urteil("switch", f"muster:{x['key']}", kanon, e is not None, tags, {"level": x["level"]},
                           gruende, x["anzahl"], x["beispiel"], d, x["belege"]))
    return out


# ── Lauf ───────────────────────────────────────────────────────────────────────

async def fenster(gl, b: datetime, ende: datetime, verz: Hostverzeichnis, d: Definition) -> tuple[list, dict]:
    """Ein 5-min-Fenster der Switches: lesen, lernen, beurteilen, Muster und Zustand sichern."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import NetzMuster, NetzZustand
    from app.services.error_clusters.collector import strip_syslog_head
    from app.services.error_clusters.miner import Miner
    from app.services.graylog_occurrences import iso

    async with AsyncSessionLocal() as db:
        z = (await db.execute(select(NetzZustand).where(NetzZustand.id == 1))).scalar_one_or_none()
        if z is None:
            z = NetzZustand(id=1, gestartet_at=datetime.now(timezone.utc))
            db.add(z)
            await db.commit()
        gestartet, state = z.gestartet_at, z.miner_state
        bekannt = set((await db.execute(select(NetzMuster.key))).scalars())

    zeilen, total = await gl.search_window(QUERY, iso(b), iso(ende), limit=10_000, fields=FELDER)
    miner = Miner(state)

    def lerne(text: str, quelle: str):
        r = miner.add(ohne_geraetename(strip_syslog_head(text, "", raw_syslog=True), quelle))
        return (r.key, r.template) if r else None

    gr = await asyncio.to_thread(gruppen, zeilen, lerne)
    treffer = bewerte_switch(gr, bekannt, gestartet, b, verz, d)

    async with AsyncSessionLocal() as db:
        neu = 0
        je_key: dict[str, dict] = defaultdict(dict)
        for x in gr:
            je_key[x["key"]] = x
        for key, x in je_key.items():
            if key in bekannt:
                continue
            db.add(NetzMuster(key=key, template=x["template"][:4000], first_seen=b, last_seen=b,
                              beispiel=x["beispiel"]))
            neu += 1
        if je_key:
            from sqlalchemy import update
            await db.execute(update(NetzMuster).where(NetzMuster.key.in_(list(je_key)))
                             .values(last_seen=b))
        z = (await db.execute(select(NetzZustand).where(NetzZustand.id == 1))).scalar_one()
        z.miner_state, z.letztes_fenster = miner.state(), b
        await db.commit()
    return treffer, {"zeilen": len(zeilen), "vollstaendig": total <= len(zeilen),
                     "muster_neu_angelegt": neu, "muster_bekannt": len(bekannt),
                     "lernphase_bis": (gestartet + LERNPHASE).isoformat()
                     if b - gestartet < LERNPHASE else None}
