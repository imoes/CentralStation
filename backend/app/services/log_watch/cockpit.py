"""Daten der KI-Cockpit-Widgets (`lw_*`) — dieselben Zahlen wie Trockenlauf und Suche.

Jede Zahl trägt Einheit (Nachrichten), Zeitraum (`zeitraum_h`) und Herkunft (`quelle`).
Widgets, die die Historie brauchen (Befunde, Netzplan), liefern solange sie aus ist den
benannten Zustand `unavailable` mit Grund — keine leere Kachel.

Hostnamen kommen hier nicht vor (Zählungen sind global, der Host-Scope eines Nutzers
gilt für Namen): nicht zugeordnete Hosts stehen namentlich nur in der Admin-API.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

TYPEN = ("lw_befunde", "lw_netzplan", "lw_kriterien", "lw_volumen", "lw_hashtags", "lw_korpus")
QUELLEN = ("graylog", "wazuh", "checkmk", "firewall", "switch")
#: Hashtag-Arten, die in Trends nichts sagen (jede Nachricht hat sie)
_OHNE_TREND = ("quelle:",)


def aktuelle_fassung(w: dict) -> bool:
    """Zeile der aktuellen Raster-Fassung (urteilt nach der Definition)? Ältere Zeilen
    tragen Hashtags und Speicherzahlen, aber keine Urteile — gemischt widersprächen sich
    die Zahlen (gemessen 06.11.: 731 Hashtag-Treffer bei 268 relevanten Nachrichten)."""
    return "je_urteil" in w


def kriterien(zeilen: list[tuple[str, datetime, dict]], definition: list[dict], stunden: int) -> dict:
    """Urteile je Kriterium und Quelle im Zeitraum — „Warum ist die Lage so?“"""
    je: dict[str, Counter] = defaultdict(Counter)
    for quelle, _b, w in zeilen:
        for k, n in (w.get("je_kriterium") or {}).items():
            je[k][quelle] += n
    titel = {k["id"]: k for k in definition}
    out = []
    for kid in sorted(je, key=lambda k: (k[0] != "R", k)):
        k = titel.get(kid, {"titel": "(unbekanntes Kriterium)", "art": "?"})
        out.append({"id": kid, "titel": k["titel"], "art": k["art"],
                    "anzahl": sum(je[kid].values()), "je_quelle": dict(je[kid])})
    return {"zeitraum_h": stunden, "einheit": "Nachrichten", "kriterien": out}


def volumen(zeilen: list[tuple[str, datetime, dict]], stunden: int) -> dict:
    """Je Quelle und Stunde: gelesen, relevant, würde gespeichert — plus Abdeckung/Ausfälle."""
    reihe: dict[str, dict[str, Counter]] = {q: defaultdict(Counter) for q in QUELLEN}
    fenster: Counter = Counter()
    fehler: Counter = Counter()
    alt: Counter = Counter()
    for quelle, b, w in zeilen:
        if quelle not in reihe:
            continue
        if "fehler" in w:
            fehler[quelle] += 1
            continue
        if not aktuelle_fassung(w):
            alt[quelle] += 1
            continue
        fenster[quelle] += 1
        h = b.replace(minute=0, second=0, microsecond=0).isoformat()
        reihe[quelle][h]["gelesen"] += w.get("nachrichten", 0)
        reihe[quelle][h]["relevant"] += (w.get("je_urteil") or {}).get("relevant", 0)
        reihe[quelle][h]["gespeichert"] += w.get("gespeichert_waere", 0)
    erwartet = stunden * 12
    return {
        "zeitraum_h": stunden, "einheit": "Nachrichten je Stunde",
        "quellen": {q: {
            "reihe": [{"stunde": h, **dict(c)} for h, c in sorted(reihe[q].items())],
            "summe": dict(sum(reihe[q].values(), Counter())),
            "fenster": fenster[q], "fenster_erwartet": erwartet, "fehler": fehler[q],
            "alte_fassung": alt[q],
        } for q in QUELLEN},
    }


def hashtags(zeilen: list[tuple[str, datetime, dict]], seit: datetime, limit: int = 15) -> dict:
    """Häufigste Hashtags relevanter Nachrichten im Zeitraum, mit Faktor gegen den gleich
    langen Zeitraum davor (> 1 = steigt). `vorher = 0` heißt neu — benannt als Faktor None."""
    jetzt: Counter = Counter()
    vorher: Counter = Counter()
    for _q, b, w in zeilen:
        if not aktuelle_fassung(w):
            continue
        ziel = jetzt if b >= seit else vorher
        for tag, n in w.get("top_hashtags") or []:
            if not tag.startswith(_OHNE_TREND):
                ziel[tag] += n
    out = []
    for tag, n in jetzt.most_common(limit):
        v = vorher.get(tag, 0)
        out.append({"tag": tag, "anzahl": n, "vorher": v,
                    "faktor": round(n / v, 1) if v else None})
    return {"einheit": "relevante Nachrichten", "hashtags": out}


def korpus(katalog: list[tuple[str, str, int]], zeilen_1h: list[tuple[str, datetime, dict]],
           letzte: dict[str, tuple[datetime, dict]], definition_v: int, historie_aktiv: bool,
           jetzt: datetime) -> dict:
    """Gesundheit der Korpora: Felder je Status, nicht zugeordnete Hosts je Grund, letzter
    Lauf je Quelle (mit Fehler), Definition, Historie."""
    felder: dict[str, Counter] = defaultdict(Counter)
    for quelle, status, n in katalog:
        felder[quelle][status] += n
    nicht_zugeordnet: Counter = Counter()
    for _q, _b, w in zeilen_1h:
        for grund, n in (w.get("nicht_zugeordnet") or {}).items():
            nicht_zugeordnet[grund] = max(nicht_zugeordnet[grund], n)
    laeufe = {}
    for q in QUELLEN:
        if q not in letzte:
            laeufe[q] = {"zustand": "nie gelaufen"}
            continue
        b, w = letzte[q]
        alter = int((jetzt - b).total_seconds() // 60)
        laeufe[q] = {"zustand": "fehler" if "fehler" in w else ("veraltet" if alter > 30 else "ok"),
                     "fenster": b.isoformat(), "alter_min": alter, "fehler": w.get("fehler")}
    return {"felder": {q: dict(c) for q, c in felder.items()},
            "nicht_zugeordnet": dict(nicht_zugeordnet), "laeufe": laeufe,
            "definition_v": definition_v, "historie_aktiv": historie_aktiv}


# ── Abfragen ───────────────────────────────────────────────────────────────────

async def _zeilen(db, seit: datetime) -> list[tuple[str, datetime, dict]]:
    from sqlalchemy import select

    from app.models.log_watch import RasterLauf
    rows = (await db.execute(select(RasterLauf).where(RasterLauf.fenster_start >= seit))).scalars()
    return [(r.quelle, r.fenster_start, r.werte) for r in rows]


async def daten(db, typ: str, cfg: dict) -> dict:
    """Daten eines `lw_*`-Widgets. Unbekannter Typ oder fehlende Historie: benannt."""
    from app.services.log_watch import historie
    from app.services.log_watch.definition import aktuelle

    jetzt = datetime.now(timezone.utc)
    stunden = min(max(int(cfg.get("hours") or 24), 1), 24 * 7)
    seit = jetzt - timedelta(hours=stunden)
    if typ in ("lw_befunde", "lw_netzplan"):
        if not await historie.aktiv(db):
            return {"state": "unavailable",
                    "error": "Historie noch nicht aktiv (Trockenlauf) — Befunde und Netzplan "
                             "entstehen erst aus gespeicherten relevanten Nachrichten"}
        return {"state": "unavailable", "error": "noch nicht gebaut"}
    if typ == "lw_kriterien":
        d = await aktuelle(db)
        return {**kriterien(await _zeilen(db, seit), d.kriterien, stunden), "definition_v": d.version}
    if typ == "lw_volumen":
        return volumen(await _zeilen(db, seit), stunden)
    if typ == "lw_hashtags":
        zeilen = await _zeilen(db, seit - timedelta(hours=stunden))
        return {**hashtags(zeilen, seit, int(cfg.get("limit") or 15)), "zeitraum_h": stunden}
    if typ == "lw_korpus":
        from sqlalchemy import func, select

        from app.models.log_watch import FieldCatalog, RasterLauf
        katalog = [(q, s, n) for q, s, n in (await db.execute(
            select(FieldCatalog.quelle, FieldCatalog.status, func.count())
            .group_by(FieldCatalog.quelle, FieldCatalog.status))).all()]
        letzte = {}
        for q in QUELLEN:
            r = (await db.execute(select(RasterLauf).where(RasterLauf.quelle == q)
                                  .order_by(RasterLauf.fenster_start.desc()).limit(1))).scalar_one_or_none()
            if r is not None:
                letzte[q] = (r.fenster_start, r.werte)
        d = await aktuelle(db)
        return korpus(katalog, await _zeilen(db, jetzt - timedelta(hours=1)), letzte, d.version,
                      await historie.aktiv(db), jetzt)
    return {"state": "unavailable", "error": f"unbekannter Widget-Typ {typ}"}
