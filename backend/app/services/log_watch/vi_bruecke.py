"""Brücke zwischen der Log-Überwachung und dem Kollektiv-Gedächtnis vi-mcp (Konnektor vi_mcp).

Drei Wege, alle mit benanntem Ausfall (ohne Kollektiv läuft die Analyse wie bisher):
  vor_analyse   vi_vorher für die Handlung „analyse“ (Reize: Hosts und Themen des Falls) + vi_erinnere nach dem,
                was ANDERE Instanzen zu diesen Hosts/Themen wissen → Abschnitt im Fall-Prompt
  nach_analyse  vi_nachher: ok, Dauer, Befund-Kern als Ergebnis (kein Rohtext der Nachrichten)
  hochschreiben Gelerntes der Intuition als Erkenntnisse: bekannte Fehler (bekannt.py) und Feldbedeutungen von KI
                oder Mensch — mit Hashtags (host:, muster:, thema:, feld:) und Quelle (Befund-ID / Zitat)

Was nie nach außen geht: Nachrichtentexte, Werkzeugergebnisse, Befund-Rohtexte (können Zugangsdaten enthalten).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

STAND_SCHLUESSEL = "log_watch.vi_bruecke_stand"
JE_LAUF = 50
#: Hashtag-Arten, die als Reize ans Kollektiv gehen (keine Verknüpfungen, keine Signale der eigenen Statistik)
REIZ_ARTEN = ("host", "thema", "muster", "dienst", "regel", "app", "kritikalitaet", "ort")
_WORT = re.compile(r"[^\w.:/-]+")
_TAG_WERT = re.compile(r"[\s()]+")


def vi_tag(tag: str) -> str | None:
    """Hashtag in die Form von vi-mcp bringen (art:wert, Wert ohne Leerzeichen/Klammern — gedaechtnis.TAG).
    CentralStation erlaubt `dienst:MSSQL Blocked Sessions`; vi-mcp lehnt das ganze Objekt ab. Rein."""
    art, _, wert = tag.partition(":")
    art = art.strip().lower()
    wert = _TAG_WERT.sub("-", wert.strip()).strip("-")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", art) or not wert:
        return None
    return f"{art}:{wert[:120]}"


def erkenntnis_aus_bekannt(e: dict) -> tuple[str, list[str], str, int, str] | None:
    """Bekannter Fehler → (text, hashtags, quelle, wichtigkeit, key). Rein — testbar. None, wenn nichts Belegbares.

    key = genau der Filter hinter „— Filter:“ (vi-mcp ≥ 0.23.0, vi_learn(key=…)): gleicher key derselben Instanz
    aktualisiert das vorhandene Objekt (voriger Text in data.history, data.repeats zählt) statt ein neues anzulegen.
    Anlass (Peer router-lernen-sklearn, 10.11.2026): 159 von 911 bekannten Fehlern waren Wiederholungen derselben
    Signatur mit höherem Zähler."""
    titel = (e.get("titel") or "").strip()
    if not titel or e.get("status") != "aktiv" or not e.get("befund"):
        return None
    tags = {vi_tag(t) for t in (e.get("hashtags") or []) if t.split(":")[0] in REIZ_ARTEN}
    tags |= {vi_tag(f"host:{h}") for h in (e.get("hosts") or [])[:10]}
    tags.add("herkunft:log-ueberwachung")
    tags.discard(None)
    rel = int(e.get("relevanz") or 0)
    key = str(e.get('filter') or e.get('fehler'))
    text = (f"Bekannter Fehler (Relevanz {rel}, {int(e.get('wiederholungen') or 0)} Wiederholungen ohne KI): {titel}"
            f" — Filter: {key}")
    return (text[:1000], sorted(tags)[:20], f"CentralStation Befund {e['befund']} (bekannt.py, {e.get('fehler')})",
            max(0, min(3, rel)), key)


def erkenntnis_aus_feld(fb) -> tuple[str, list[str], str, int] | None:
    """Feldbedeutung (FeldBedeutung) von KI oder Mensch → Erkenntnis. Rein — testbar."""
    if getattr(fb, "herkunft", "") not in ("ki", "mensch"):
        return None
    name = getattr(fb, "name", "") or ""
    art, _, rest = name.partition(":")
    tags = [f"feld:{_WORT.sub('-', rest)[:60]}", f"feldart:{art}", "herkunft:log-ueberwachung"]
    text = f"Bedeutung von {name}: {(getattr(fb, 'bedeutung', '') or '').strip()}"
    return text[:1000], tags, f"CentralStation feld_bedeutung ({fb.herkunft}): {(getattr(fb, 'quelle', '') or '')[:300]}", 1


def reize_aus_fall(fall: dict, lage: dict | None = None) -> list[str]:
    """Hinweisreize einer Analyse: Hosts des Falls, Start-Hashtag, Themen der Lage. Rein — testbar."""
    reize = [f"host:{h}" for h in (fall.get("hosts") or [])[:8]]
    start = fall.get("start") or ""
    if ":" in start and start.split(":")[0] in REIZ_ARTEN:
        reize.append(start)
    for t in (lage or {}).get("facetten", {}).get("thema", [])[:4]:
        w = t.get("wert") if isinstance(t, dict) else None
        if w:
            reize.append(f"thema:{w}")
    return list(dict.fromkeys(reize))


def filter_andere(reize: list[str]) -> str:
    """Was ANDERE Instanzen dazu wissen — Filtersprache von vi-mcp."""
    from app.services.connectors.vi_mcp import INSTANZ
    return "(" + " OR ".join(reize) + f") !instanz:{INSTANZ} !art:antwort !art:erfahrung"


#: Antworttexte von vi-mcp — deutsch bis 0.22, englisch ab 0.23.0 (Peer 10.11.2026: „alle Ausgaben … sind englisch“).
#: Beide Formen gelten, weil lokale VI und Kollektiv unterschiedliche Versionen haben können.
ABGELEHNT = ("abgelehnt", "rejected")
NICHTS = ("nichts im Gedächtnis", "nothing in memory")
SELBST = ("[Selbst]", "[Self]")
LAGER = ("Im Langzeitlager", "long-term archive")   # 0.23.0: „Matching in the long-term archive: …“ (archive.py:220)


def abgelehnt(antwort: str) -> bool:
    """Hat vi-mcp den Lerneintrag abgelehnt? Zuerst das MCP-Feld isError (ViAntwort.fehler, sprachunabhängig),
    sonst der Textanfang (Antworten ohne isError, z. B. aus Tests). Rein."""
    return bool(getattr(antwort, "fehler", False)) or str(antwort or "").lstrip().startswith(ABGELEHNT)


def kein_fallwissen(hinweis: str) -> bool:
    """Selbst-Erzählung der VI oder Langzeitlager-Hinweis — gehört nicht in den Fall-Prompt. Rein."""
    h = str(hinweis)
    return h.startswith(SELBST) or any(x in h for x in LAGER)


def abschnitt(vorher: dict | str | None, erinnert: str, fehler: str | None) -> str:
    """Der Prompt-Abschnitt „Kollektiv-Gedächtnis“ — auch der Ausfall ist benannt. Rein."""
    z = ["Kollektiv-Gedächtnis (vi-mcp, andere Instanzen — Kontext, kein Beleg):"]
    if fehler:
        z.append(f"  nicht erreichbar: {fehler}")
        return "\n".join(z)
    hinweise = (vorher or {}).get("hinweise") if isinstance(vorher, dict) else None
    # kein Fallwissen: „[Selbst] …“ (Selbst-Erzählung der VI, vi-mcp ≥ 0.4.0) und „Im Langzeitlager passen: …“
    # (seit 0.10.0: ≥ 180 Tage unbenutzte Einträge — zu alt für die Lage)
    for h in [h for h in (hinweise or []) if not kein_fallwissen(h)][:6]:
        z.append("  " + str(h)[:300])
    if erinnert and not erinnert.startswith(NICHTS):
        z += ["  " + l[:300] for l in erinnert.splitlines()[1:9]]
    if len(z) == 1:
        z.append("  nichts bekannt")
    return "\n".join(z)


async def vor_analyse(fall: dict, lage: dict | None = None) -> tuple[str, str | None]:
    """(Prompt-Abschnitt, vorher_id). Ohne Konnektor: ('', None) — die Analyse merkt nichts."""
    from app.core.database import AsyncSessionLocal
    from app.services.connectors.vi_mcp import ViMcpFehler, load_vi_mcp_connector
    async with AsyncSessionLocal() as db:
        k = await load_vi_mcp_connector(db)
    if k is None:
        return "", None
    reize = reize_aus_fall(fall, lage)
    try:
        v = await k.vorher("analyse", {"start": fall.get("start"), "hosts": (fall.get("hosts") or [])[:8]},
                           lesend=False, kontext_hashtags=reize)
        er = await k.erinnere(filter_andere(reize), limit=6) if reize else ""
        return abschnitt(v, er, None), (v.get("vorher_id") if isinstance(v, dict) else None)
    except ViMcpFehler as e:
        log.warning("vi-mcp vor Analyse: %s", e)
        return abschnitt(None, "", str(e)), None
    except Exception as e:  # noqa: BLE001
        log.warning("vi-mcp vor Analyse: %s", e)
        return abschnitt(None, "", f"{type(e).__name__}: {e}"), None


async def nach_analyse(fall: dict, ergebnis: dict, dauer_ms: int, vorher_id: str | None) -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.connectors.vi_mcp import load_vi_mcp_connector
    async with AsyncSessionLocal() as db:
        k = await load_vi_mcp_connector(db)
    if k is None:
        return
    ok = "befund" in ergebnis
    kern = f"{ergebnis.get('titel', '')} (Relevanz {ergebnis.get('relevanz')}, Befund {ergebnis.get('befund')})" if ok else None
    try:
        await k.nachher("analyse", {"start": fall.get("start"), "hosts": (fall.get("hosts") or [])[:8]}, ok, dauer_ms,
                        vorher_id=vorher_id, fehler=None if ok else str(ergebnis.get("kein_befund") or ergebnis.get("fehler") or "kein Befund"),
                        ergebnis=kern)
    except Exception as e:  # noqa: BLE001 — die Meldung darf die Analyse nicht stören
        log.warning("vi-mcp nach Analyse: %s", e)


async def hochschreiben() -> dict:
    """Gelerntes seit dem letzten Stand als Erkenntnisse nach vi-mcp. Ergebnis für raster_lauf „vi-bruecke“."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import FeldBedeutung
    from app.services.connectors.vi_mcp import ViMcpFehler, load_vi_mcp_connector
    from app.services.log_watch import bekannt
    from app.services.settings import get_setting, set_setting
    t0 = time.monotonic()
    async with AsyncSessionLocal() as db:
        k = await load_vi_mcp_connector(db)
        stand = await get_setting(db, STAND_SCHLUESSEL) or "1970-01-01T00:00:00+00:00"
        felder = (await db.execute(select(FeldBedeutung).where(FeldBedeutung.angelegt_at > datetime.fromisoformat(stand),
                                                                FeldBedeutung.herkunft.in_(("ki", "mensch")))
                                   .order_by(FeldBedeutung.angelegt_at).limit(JE_LAUF))).scalars().all()
    if k is None:
        return {"ausgesetzt": "kein vi_mcp-Konnektor aktiviert"}
    bek = await bekannt.liste(status="aktiv", limit=500)           # Liste ist nach zuletzt sortiert, nicht nach gelernt
    eintraege = bek.get("eintraege") or bek.get("liste") or (bek if isinstance(bek, list) else [])
    neu_bek = sorted((e for e in eintraege if (e.get("gelernt") or "") > stand), key=lambda e: e["gelernt"])[:JE_LAUF]
    geschrieben, zurueckgewiesen, fehler = 0, 0, None
    neuer_stand = stand
    try:
        for e in neu_bek:
            erk = erkenntnis_aus_bekannt(e)
            if erk:
                r = await k.lerne(*erk, ausgang="schlecht")      # ein bekannter Fehler ist eine Störung
                abg = abgelehnt(r)
                geschrieben += 0 if abg else 1
                zurueckgewiesen += 1 if abg else 0
            neuer_stand = max(neuer_stand, e.get("gelernt") or neuer_stand)
        for fb in felder:
            erk = erkenntnis_aus_feld(fb)
            if erk:
                r = await k.lerne(*erk)
                abg = abgelehnt(r)
                geschrieben += 0 if abg else 1
                zurueckgewiesen += 1 if abg else 0
            neuer_stand = max(neuer_stand, fb.angelegt_at.isoformat())
    except ViMcpFehler as e:
        fehler = str(e)
    if neuer_stand != stand and not fehler:
        async with AsyncSessionLocal() as db:
            await set_setting(db, STAND_SCHLUESSEL, neuer_stand)
            await db.commit()
    return {"geschrieben": geschrieben, "abgelehnt": zurueckgewiesen, "bekannte_fehler": len(neu_bek), "felder": len(felder),
            "stand": neuer_stand, "dauer_ms": int((time.monotonic() - t0) * 1000), **({"fehler": fehler} if fehler else {})}
