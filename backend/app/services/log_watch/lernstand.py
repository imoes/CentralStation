"""Lernstand: die Ergebnisse vergleichen — wird das Training besser, und was bewirkt es in Produktion?

Nutzer 12.11.2026: „kein schattenbetrieb wir testen das in produktion. Du musst nur die ergebnisse vergleichen können
um die effizients fest zu stellen und ob das training das ergebnis verbessert“.

Drei Blicke, jede Zahl mit Zeitraum und Herkunft:
1. Verlauf je Trainingslauf (stündlich): Kennzahlen jeder gespeicherten Version — Relevanz-Modell (cs-log-modell),
   Auffälligkeiten (cs-log-auffaellig), Lösungswege (cs-log-loesungen) — mit Trend (erste, letzte, Steigung je Lauf).
2. Wirkung in Produktion (raster_lauf): je Stunde, die letzten N Stunden gegen die N davor — weitergeleitet, vom
   Modell geblockt, von Auffälligkeiten weitergeleitet, KI-Analysen.
3. Treffsicherheit in Produktion (cs-logs-*, Urteil der KI):
   - Erkundung: Fälle, die das Modell geblockt HÄTTE (5 % Zufall) — Anteil, den die KI auch Rauschen nennt;
   - Auffälligkeiten: Fälle, die erst R4-AUF weiterleitete — Anteil, den die KI relevant nennt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def trend(werte: list[float | None]) -> dict:
    """Erste, letzte, Differenz und Steigung je Lauf (kleinste Quadrate) über die vorhandenen Werte. Rein."""
    pts = [(i, float(w)) for i, w in enumerate(werte) if w is not None]
    if not pts:
        return {"n": 0}
    n = len(pts)
    out = {"n": n, "erste": round(pts[0][1], 4), "letzte": round(pts[-1][1], 4),
           "differenz": round(pts[-1][1] - pts[0][1], 4)}
    if n >= 3:
        mx = sum(i for i, _ in pts) / n
        my = sum(w for _, w in pts) / n
        sxx = sum((i - mx) ** 2 for i, _ in pts)
        out["steigung_je_lauf"] = round(sum((i - mx) * (w - my) for i, w in pts) / sxx, 5) if sxx else 0.0
    return out


def anteil(treffer: int, n: int) -> float | None:
    return round(treffer / n, 4) if n else None


def _hole(d: dict, pfad: str):
    for teil in pfad.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(teil)
    return d


async def _verlauf(index: str, felder: dict[str, str], n: int) -> list[dict]:
    from app.core.opensearch import get_opensearch
    try:
        r = await get_opensearch().search(index=index, ignore_unavailable=True,
                                          body={"size": n, "sort": [{"zeit": "desc"}]})
    except Exception:  # noqa: BLE001
        return []
    zeilen = [{name: _hole(h["_source"], pfad) for name, pfad in felder.items()} for h in r["hits"]["hits"]]
    return list(reversed(zeilen))            # älteste zuerst — der Trend liest von links nach rechts


def summe_fenster(zeilen: list[tuple[str, datetime, dict]], von: datetime, bis: datetime) -> dict:
    """Wirkung im Zeitraum aus raster_lauf-Zeilen (quelle, fenster_start, werte). Rein."""
    s = {"nachrichten": 0, "relevant": 0, "modell_geblockt": 0, "modell_erkundung": 0, "modell_signal": 0,
         "auffaellig_signal": 0, "auffaellig_weitergeleitet": 0, "ki_analysen": 0, "fenster": 0}
    for quelle, b, w in zeilen:
        if not (von <= b < bis) or not isinstance(w, dict):
            continue
        if quelle == "ki":
            s["ki_analysen"] += 1
            continue
        if "nachrichten" not in w:
            continue
        s["fenster"] += 1
        s["nachrichten"] += w.get("nachrichten") or 0
        s["relevant"] += (w.get("je_urteil") or {}).get("relevant", 0)
        m = w.get("modell_rauschen") or {}
        s["modell_signal"] += m.get("nachrichten") or 0
        s["modell_geblockt"] += m.get("entschied") or 0
        s["modell_erkundung"] += m.get("erkundung") or 0
        a = w.get("auffaellig") or {}
        s["auffaellig_signal"] += a.get("nachrichten") or 0
        s["auffaellig_weitergeleitet"] += a.get("entschied") or 0
    stunden = max((bis - von).total_seconds() / 3600, 1e-9)
    s["je_stunde"] = {k: round(v / stunden, 2) for k, v in s.items() if k not in ("fenster",)}
    return s


async def vergleich(stunden: float = 24, n: int = 48) -> dict:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.opensearch import get_opensearch
    from app.models.log_watch import RasterLauf
    jetzt = datetime.now(timezone.utc)
    rel = await _verlauf("cs-log-modell", {
        "zeit": "zeit", "modus": "modus", "variante": "variante", "varianten": "varianten",
        "schicht_modus": "schicht.modus", "schicht_grund": "schicht.grund", "schicht_deltas": "schicht.deltas",
        "schicht_offline": "schicht.messung.offline", "schicht_prospektiv": "schicht.messung.prospektiv",
        "auc_ungesehen": "kennzahlen.auc", "auc_je_schluessel": "kennzahlen.auc_gruppen",
        "auc_zeitlich": "kennzahlen.auc_zeitlich", "recall_relevant": "kennzahlen.recall_relevant",
        "rauschen_erkannt": "kennzahlen.rauschen_erkannt", "folge_rauschen": "kennzahlen.folge_rauschen",
        "folge_relevant": "kennzahlen.folge_relevant", "labels": "labels.gesamt", "schwelle": "schwelle"}, n)
    auf = await _verlauf("cs-log-auffaellig", {
        "zeit": "zeit", "ausreisser": "vergleich.ausreisser", "zeilen": "vergleich.zeilen",
        "auc_gegen_ki": "vergleich.auc_gegen_ki_relevant", "regeln_uebersehen": "vergleich.regeln_uebersehen",
        "schwelle": "schwelle"}, n)
    loe = await _verlauf("cs-log-loesungen", {
        "zeit": "zeit", "episoden": "episoden", "mit_massnahme": "mit_massnahme", "modi": "modi",
        "holdout_gehalten": "holdout.gehalten", "holdout_geprueft": "holdout.gepruefte_paare"}, n)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(RasterLauf).where(
            RasterLauf.fenster_start >= jetzt - timedelta(hours=2 * stunden)))).scalars().all()
    zeilen = [(r.quelle, r.fenster_start, r.werte) for r in rows]
    jetzt_f = summe_fenster(zeilen, jetzt - timedelta(hours=stunden), jetzt)
    vorher_f = summe_fenster(zeilen, jetzt - timedelta(hours=2 * stunden), jetzt - timedelta(hours=stunden))
    o = get_opensearch()

    async def zaehl(signal: str, kriterium: str) -> dict:
        r = await o.search(index="cs-logs-*", ignore_unavailable=True, body={
            "size": 0, "track_total_hits": True,
            "query": {"bool": {"filter": [{"term": {"hashtags": signal}}]}},
            "aggs": {"ki": {"filter": {"exists": {"field": "ai_relevanz"}},
                            "aggs": {"r": {"range": {"field": "ai_relevanz",
                                                     "ranges": [{"key": "rauschen", "to": 2},
                                                                {"key": "relevant", "from": 2}]}}}}}})
        b = {x["key"]: x["doc_count"] for x in r["aggregations"]["ki"]["r"]["buckets"]}
        n_ki = r["aggregations"]["ki"]["doc_count"]
        return {"gesamt": r["hits"]["total"]["value"], "von_ki_beurteilt": n_ki,
                "ki_rauschen": b.get("rauschen", 0), "ki_relevant": b.get("relevant", 0),
                "trefferquote": anteil(b.get(kriterium, 0), n_ki)}
    return {
        "zeit": jetzt.isoformat(),
        "training": {
            "relevanz_modell": {"laeufe": rel, "trend_auc_ungesehen": trend([z["auc_ungesehen"] for z in rel]),
                                "trend_recall_relevant": trend([z["recall_relevant"] for z in rel])},
            "auffaelligkeiten": {"laeufe": auf, "trend_auc_gegen_ki": trend([z["auc_gegen_ki"] for z in auf])},
            "loesungswege": {"laeufe": loe, "trend_mit_massnahme": trend([z["mit_massnahme"] for z in loe])}},
        "produktion": {"zeitraum_h": stunden, "jetzt": jetzt_f, "vorher": vorher_f,
                       "quelle": "raster_lauf (je 5-min-Fenster und Quelle) und raster_lauf „ki“ (je KI-Analyse)"},
        "treffsicherheit": {
            "modell_erkundung": {**await zaehl("signal:modell-erkundung", "rauschen"),
                                 "bedeutung": "Trefferquote = Anteil, den auch die KI Rauschen nennt (Modell lag richtig)"},
            "auffaellig_weitergeleitet": {**await zaehl("signal:auffaellig-entschied", "relevant"),
                                          "bedeutung": "Trefferquote = Anteil, den die KI relevant nennt"},
            "modell_geblockt_gespeichert": (await zaehl("signal:modell-entschied", "rauschen"))["gesamt"]},
    }
