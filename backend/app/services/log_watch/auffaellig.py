"""Auffälligkeiten: ein IsolationForest (scikit-learn) lernt das NORMALE Verhalten jedes Drain-Musters und bewertet
die jüngsten Fünf-Minuten-Fenster — Schattenbetrieb neben den Statistikregeln (error_clusters/rules.py).

Nutzer 12.11.2026: „recherchiere wie man eine ki mit scikit learn oder pytorch macht. damit wir aufälligkeiten und
lösungen direkt als vi trainieren“ — „ja mache das“ — „es soll gleich trainieren ich muss da nichts mehr machen“.
Rat der vi-mcp-Sitzung: scikit-learn statt PyTorch (kleine Daten, lesbar, keine GPU); das Modell bleibt in
CentralStation, in die VI gehört das Ergebnis. scikit-learn-Doku „Novelty and Outlier Detection“: Training auf (fast)
normalen Daten, Bewertung neuer Beobachtungen.

Beobachtung = (Muster, 5-min-Bucket) aus log_template_buckets. Merkmale (alle aus vorhandenen Zählern, ohne Uhrzeit):
  menge     log(1 + Anzahl)
  ueber     log(1 + Anzahl) − log(1 + mittlere Anzahl des Musters im Training)   — Abweichung vom eigenen Normal
  hosts     log(1 + Zahl betroffener Hosts/Container)
  alter     log(1 + Stunden seit dem ersten Auftreten des Musters)               — Neuheit
  praesenz  Anteil der Trainings-Buckets, in denen das Muster vorkam            — selten vs. Dauerrauschen
Wirkung (Nutzer 12.11.2026: „kein schattenbetrieb wir testen das in produktion“): das trainierte Modell liegt im
Speicher des Backends (stündlich neu, nach einem Neustart sofort); raster.mit_modell bewertet jedes Graylog-Fenster
je Muster und setzt bei Ausreißern `signal:auffaellig` mit Grund — bis 13.11. leitete die Regel R4-AUF solche Fenster weiter (entfernt, Definition 19); jetzt wirkt der Wert nur als Merkmal im Relevanz-Modell,
auch wenn die Regeln sie sonst verworfen hätten. Gemessen wird über lernstand.py.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

INDEX = "cs-log-auffaellig"
SIGNAL = "signal:auffaellig"
MERKMALE = ("menge", "ueber", "hosts", "alter", "praesenz")
TRAIN_TAGE = 14
BEWERTE_STUNDEN = 24
STICHPROBE = 120_000
BUCKET_S = 300
#: Ausreißer = Auffälligkeit über diesem Quantil der Trainingswerte (≈ 0,5 % der Muster-Fenster). contamination="auto"
#: (decision_function < 0) wäre für eine Regel, die weiterleitet, zu weit — benannt statt still gewählt
QUANTIL = 0.995


def merkmal_zeile(anzahl: int, mittel: float, hosts: int, alter_h: float, praesenz: float) -> list[float]:
    """Die fünf Merkmale einer Beobachtung. Rein."""
    m = math.log1p(max(0, anzahl))
    return [m, m - math.log1p(max(0.0, mittel)), math.log1p(max(0, hosts)), math.log1p(max(0.0, alter_h)),
            max(0.0, min(1.0, praesenz))]


def grund(x: list[float], normal: list[tuple[float, float]]) -> list[str]:
    """Welche Merkmale vom Normal (Mittel, Streuung im Training) am stärksten abweichen — lesbare Begründung. Rein."""
    z = []
    for name, wert, (mu, sd) in zip(MERKMALE, x, normal):
        if sd > 0:
            z.append((abs(wert - mu) / sd, name, wert, mu))
    z.sort(reverse=True)
    return [f"{n} {w:.2f} (üblich {m:.2f}, {s:.1f} σ)" for s, n, w, m in z[:3] if s >= 2]


def trainiere(x_train: list[list[float]], seed: int = 0):
    from sklearn.ensemble import IsolationForest
    return IsolationForest(n_estimators=200, max_samples=512, contamination="auto", random_state=seed).fit(x_train)


def normal(x_train: list[list[float]]) -> list[tuple[float, float]]:
    import numpy as np
    a = np.asarray(x_train, dtype=float)
    return [(float(a[:, i].mean()), float(a[:, i].std())) for i in range(a.shape[1])]


def auc(scores: list[float], y: list[int]) -> float | None:
    from app.services.log_watch.relevanz_modell import auc as _auc
    return _auc(scores, y)


def bewerte(modell, normal_: list[tuple[float, float]], zeilen: list[dict], schwelle: float) -> list[dict]:
    """Jede Zeile bekommt `auffaellig` (höher = ungewöhnlicher, −score_samples), `ausreisser` (≥ schwelle) und `grund`.
    Rein bis auf das Modell."""
    if not zeilen:
        return []
    s = modell.score_samples([z["x"] for z in zeilen])
    for z, si in zip(zeilen, s):
        z["auffaellig"] = round(float(-si), 4)
        z["ausreisser"] = bool(-si >= schwelle)
        z["grund"] = grund(z["x"], normal_)
    return zeilen


def schwelle_aus(modell, x_train: list[list[float]], q: float = QUANTIL) -> float:
    import numpy as np
    return float(np.quantile(-modell.score_samples(x_train), q))


def vergleich(zeilen: list[dict], weitergeleitet: set[tuple[str, int]], ki: dict[tuple[str, int], int]) -> dict:
    """Schatten-Vergleich: deckt sich „auffällig“ mit dem, was die Regeln weiterleiten und was die KI relevant nennt?
    weitergeleitet = (Muster, Bucket) mit gespeicherter Nachricht; ki = (Muster, Bucket) → 1 relevant / 0 Rauschen. Rein."""
    aus = [z for z in zeilen if z["ausreisser"]]
    nicht = [z for z in zeilen if not z["ausreisser"]]

    def anteil(liste):
        return round(sum(1 for z in liste if (z["key"], z["b"]) in weitergeleitet) / len(liste), 4) if liste else None

    mit_ki = [z for z in zeilen if (z["key"], z["b"]) in ki]
    return {"zeilen": len(zeilen), "ausreisser": len(aus),
            "regeln_leiteten_weiter_bei_ausreissern": anteil(aus),
            "regeln_leiteten_weiter_sonst": anteil(nicht),
            "ki_beurteilt": len(mit_ki),
            # nur innerhalb dessen, was die Regeln weitergeleitet haben (nur dort urteilt die KI) — benannt
            "auc_gegen_ki_relevant": auc([z["auffaellig"] for z in mit_ki], [ki[(z["key"], z["b"])] for z in mit_ki]),
            "regeln_uebersehen": sum(1 for z in aus if (z["key"], z["b"]) not in weitergeleitet)}


# ── Daten ─────────────────────────────────────────────────────────────────────────────────────────────────
async def _zeilen(db, von: datetime, bis: datetime, stichprobe: int | None, stats: dict, erst: dict) -> list[dict]:
    from sqlalchemy import text
    q = ("select key, bucket_start, count, n_entities from log_template_buckets "
         "where bucket_start >= :von and bucket_start < :bis and complete")
    if stichprobe:
        q += " order by random() limit :n"
    rows = (await db.execute(text(q), {"von": von, "bis": bis, "n": stichprobe})).all()
    out = []
    for key, b, n, h in rows:
        mittel, praesenz = stats.get(key, (0.0, 0.0))
        alter = (b - erst[key]).total_seconds() / 3600 if key in erst else 0.0
        out.append({"key": key, "b": int(b.timestamp()) // BUCKET_S, "zeit": b.isoformat(), "anzahl": int(n),
                    "hosts": int(h or 0), "x": merkmal_zeile(int(n), mittel, int(h or 0), alter, praesenz)})
    return out


_STAND: dict | None = None


def setze(stand: dict | None) -> None:
    global _STAND
    _STAND = stand


def aktuelles() -> dict | None:
    return _STAND


def pruefe_fenster(treffer, b: datetime | None, stand: dict | None = None
                   ) -> dict[tuple[str, str], tuple[float, list, bool]]:
    """Graylog-Treffer eines Fensters je Muster zusammenfassen (Anzahl, Hosts) und bewerten:
    {(quelle, schluessel): (Auffälligkeit, Grund, Ausreißer)} — alle Muster, denn der Wert ist auch Merkmal des
    Relevanz-Modells (Variante „auffaellig“). Rein bis auf das Modell."""
    st = stand if stand is not None else _STAND
    if not st:
        return {}
    gruppen: dict[str, dict] = {}
    for t in treffer:
        if t.quelle == "graylog" and t.schluessel.startswith("muster:"):
            g = gruppen.setdefault(t.schluessel, {"anzahl": 0, "hosts": set()})
            g["anzahl"] += t.anzahl
            g["hosts"].add(t.host)
    if not gruppen:
        return {}
    zeilen = []
    for schl, g in gruppen.items():
        key = schl[7:]
        mittel, praesenz = st["stats"].get(key, (0.0, 0.0))
        erst = st["erst"].get(key)
        alter = (b - erst).total_seconds() / 3600 if (b and erst) else 0.0
        zeilen.append({"key": key, "b": 0, "x": merkmal_zeile(g["anzahl"], mittel, len(g["hosts"]), alter, praesenz),
                       "schluessel": schl})
    bewerte(st["modell"], st["normal"], zeilen, st["schwelle"])
    return {("graylog", z["schluessel"]): (z["auffaellig"], z["grund"], z["ausreisser"]) for z in zeilen}


async def lerne(jetzt: datetime | None = None) -> dict:
    """Stündlich: auf den 14 Tagen vor den letzten 24 h trainieren, die letzten 24 h bewerten, mit Regeln und KI
    vergleichen, Ergebnis nach cs-log-auffaellig. Nur lesend auf PostgreSQL und cs-logs-*."""
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.core.opensearch import get_opensearch
    jetzt = jetzt or datetime.now(timezone.utc)
    grenze = jetzt - timedelta(hours=BEWERTE_STUNDEN)
    start = grenze - timedelta(days=TRAIN_TAGE)
    async with AsyncSessionLocal() as db:
        buckets_gesamt = TRAIN_TAGE * 86400 // BUCKET_S
        stats = {k: (float(m), float(c) / buckets_gesamt) for k, m, c in (await db.execute(text(
            "select key, avg(count), count(*) from log_template_buckets where bucket_start >= :von "
            "and bucket_start < :bis and complete group by key"), {"von": start, "bis": grenze})).all()}
        erst = {k: f for k, f in (await db.execute(text("select key, first_seen from log_templates"))).all()}
        train = await _zeilen(db, start, grenze, STICHPROBE, stats, erst)
        neu = await _zeilen(db, grenze, jetzt, None, stats, erst)
        vorlagen = {k: t for k, t in (await db.execute(text("select key, template from log_templates"))).all()}
    if len(train) < 1000:
        return {"zeit": jetzt.isoformat(), "modus": "zu_wenig_daten", "grund": f"{len(train)} Trainingszeilen"}
    modell = trainiere([z["x"] for z in train])
    nm = normal([z["x"] for z in train])
    schwelle = schwelle_aus(modell, [z["x"] for z in train])
    setze({"modell": modell, "normal": nm, "stats": stats, "erst": erst, "zeit": jetzt.isoformat(),
           "schwelle": schwelle, "q90": schwelle_aus(modell, [z["x"] for z in train], 0.9)})
    bewerte(modell, nm, neu, schwelle)
    # Regeln und KI: gespeicherte Graylog-Nachrichten der letzten 24 h je (Muster, Bucket)
    weiter, ki = set(), {}
    r = await get_opensearch().search(index="cs-logs-*", ignore_unavailable=True, body={
        "size": 10000, "query": {"bool": {"filter": [{"term": {"quelle": "graylog"}},
                                                    {"range": {"zeit": {"gte": grenze.isoformat()}}}]}},
        "_source": ["schluessel", "zeit", "ai_relevanz"]})
    for h in r["hits"]["hits"]:
        s = h["_source"]
        k = str(s.get("schluessel") or "")
        if not k.startswith("muster:"):
            continue
        try:
            b = int(datetime.fromisoformat(str(s["zeit"]).replace("Z", "+00:00")).timestamp()) // BUCKET_S
        except (KeyError, ValueError):
            continue
        weiter.add((k[7:], b))
        if s.get("ai_relevanz") not in (None, ""):
            ki[(k[7:], b)] = 1 if int(s["ai_relevanz"]) >= 2 else 0
    vg = vergleich(neu, weiter, ki)
    top = sorted((z for z in neu if z["ausreisser"]), key=lambda z: -z["auffaellig"])

    def zeige(z):
        return {"muster": z["key"], "zeit": z["zeit"], "anzahl": z["anzahl"], "hosts": z["hosts"],
                "auffaellig": z["auffaellig"], "grund": z["grund"], "regeln": (z["key"], z["b"]) in weiter,
                "text": str(vorlagen.get(z["key"]) or "")[:200]}
    erg = {"zeit": jetzt.isoformat(), "modus": "aktiv",
           "grund": "wirkt als Merkmal im Relevanz-Modell (Variante auffaellig; R4-AUF entfernt 13.11.); Vergleich mit Regeln und KI unten",
           "schwelle": round(schwelle, 4), "quantil": QUANTIL,
           "training": {"von": start.isoformat(), "bis": grenze.isoformat(), "zeilen": len(train),
                        "muster": len(stats), "normal": {n: {"mittel": round(m, 3), "streuung": round(s, 3)}
                                                         for n, (m, s) in zip(MERKMALE, nm)}},
           "bewertet": {"von": grenze.isoformat(), "bis": jetzt.isoformat()}, "vergleich": vg,
           "top": [zeige(z) for z in top[:30]],
           "regeln_uebersehen_top": [zeige(z) for z in top if (z["key"], z["b"]) not in weiter][:30]}
    os_ = get_opensearch()
    if not await os_.indices.exists(index=INDEX):
        await os_.indices.create(index=INDEX, body={"mappings": {"dynamic": False,
                                                                 "properties": {"zeit": {"type": "date"}}}})
    await os_.index(index=INDEX, id=erg["zeit"], body=erg, refresh=True)
    log.info("Auffälligkeiten: %s Zeilen, %s Ausreißer, %s nicht von Regeln weitergeleitet", vg["zeilen"],
             vg["ausreisser"], vg["regeln_uebersehen"])
    return erg


async def lade() -> dict | None:
    from app.core.opensearch import get_opensearch
    try:
        r = await get_opensearch().search(index=INDEX, ignore_unavailable=True,
                                          body={"size": 1, "sort": [{"zeit": "desc"}]})
    except Exception as e:  # noqa: BLE001
        log.warning("Auffälligkeiten nicht geladen: %s", e)
        return None
    h = r.get("hits", {}).get("hits", [])
    return h[0]["_source"] if h else None
