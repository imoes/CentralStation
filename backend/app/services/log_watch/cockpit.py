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

TYPEN = ("lw_befunde", "lw_netzplan", "lw_kriterien", "lw_volumen", "lw_hashtags", "lw_korpus", "lw_ki")
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


def ki(zeilen: list[tuple[str, datetime, dict]], stunden: int) -> dict:
    """Statistik der KI-Flotte. Zwei Quellen, getrennt gemessen:
    - `ki-pruefung` (alle 10 min ein Messpunkt): Warteschlange — offene Nachrichten, Fälle, verfallen;
    - `ki` (je Agent-Einsatz): analysierte Fälle, Befunde, ohne Befund, Fehler, Modellzeit.
    So zählt „verfallen“ einmal je Takt, nicht je Agent, und die Kurve hat auch ohne Arbeit Punkte."""
    reihe, summe = [], Counter()
    einsaetze, modell_s, analysiert = 0, 0.0, 0
    for quelle, b, w in sorted(zeilen, key=lambda z: z[1]):
        if quelle == "ki-pruefung" and "fehler" not in w:
            reihe.append({"zeit": b.isoformat(), "offen": w.get("offen", 0), "faelle": w.get("faelle", 0),
                          "verfallen": w.get("verfallen", 0), "bekannt": w.get("bekannt", 0)})
            summe["verfallen"] += w.get("verfallen", 0)
        elif quelle == "ki-pruefung":
            summe["pruef_fehler"] += 1
        elif quelle == "ki":
            einsaetze += 1
            an = w.get("analysen") or []
            summe["analysiert"] += len(an)
            summe["befunde"] += len(w.get("befunde") or [])
            summe["ohne_befund"] += sum(1 for a in an if "kein_befund" in a)
            summe["fehler"] += sum(1 for a in an if "fehler" in a)
            # Lernen: Wiederholungen bekannter Fehler, ohne KI an ihren Befund gehängt (Nachrichten)
            summe["ohne_ki"] += w.get("bekannt_angehaengt", 0)
            summe["gelernt"] += sum(a.get("gelernt") or 0 for a in an)
            modell_s += sum(s.get("ki_s") or 0 for a in an for s in a.get("schritte") or [])
            analysiert += len(an)
    for k in ("analysiert", "befunde", "ohne_befund", "fehler", "verfallen", "ohne_ki", "gelernt"):
        summe.setdefault(k, 0)
    return {"zeitraum_h": stunden, "einheit": "Nachrichten bzw. Fälle; Messpunkt alle 10 min",
            "laeufe": einsaetze, "messpunkte": len(reihe), "reihe": reihe, "summe": dict(summe),
            "jetzt": reihe[-1] if reihe else None,
            "modell_s_je_fall": round(modell_s / analysiert, 1) if analysiert else None,
            # Beobachtungspunkt: wächst die Warteschlange über den Zeitraum, kommt die Flotte nicht nach
            "trend_offen": (reihe[-1]["offen"] - reihe[0]["offen"]) if len(reihe) >= 2 else None}


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
    langen Zeitraum davor (> 1 = steigt).

    `vergleich` je Hashtag, erschöpfend: „faktor“ (beide Zeiträume gemessen), „neu“ (davor
    gemessen, dort nicht vorgekommen), „kein_vergleich“ (der Zeitraum davor hat keine Fenster
    dieser Quelle in aktueller Fassung — „neu“ wäre eine falsche Behauptung; gemessen 06.11.:
    nach einem Fassungswechsel stand jeder Hashtag auf „neu“)."""
    jetzt: Counter = Counter()
    vorher: Counter = Counter()
    vorher_gemessen: set[str] = set()          # Quellen mit Fenstern im Vergleichszeitraum
    quelle_je_tag: dict[str, str] = {}
    for q, b, w in zeilen:
        if not aktuelle_fassung(w):
            continue
        if b < seit:
            vorher_gemessen.add(q)
        ziel = jetzt if b >= seit else vorher
        for tag, n in w.get("top_hashtags") or []:
            if not tag.startswith(_OHNE_TREND):
                ziel[tag] += n
                if ziel is jetzt:
                    quelle_je_tag.setdefault(tag, q)
    out = []
    for tag, n in jetzt.most_common(limit):
        v = vorher.get(tag, 0)
        if v:
            vergleich, faktor = "faktor", round(n / v, 1)
        elif quelle_je_tag.get(tag) in vorher_gemessen:
            vergleich, faktor = "neu", None
        else:
            vergleich, faktor = "kein_vergleich", None
        out.append({"tag": tag, "anzahl": n, "vorher": v, "faktor": faktor, "vergleich": vergleich})
    return {"einheit": "relevante Nachrichten", "hashtags": out}


def korpus(katalog: list[tuple[str, str, int]], zeilen_1h: list[tuple[str, datetime, dict]],
           letzte: dict[str, tuple[datetime, dict]], definition_v: int, historie_aktiv: bool,
           jetzt: datetime, stufe0: dict | None = None) -> dict:
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
    s0 = None
    if stufe0 is not None:
        s0 = ({"zustand": "fehler", "fehler": stufe0["fehler"]} if "fehler" in stufe0 else
              {"zustand": "ok" if stufe0.get("vollstaendig") else "unvollstaendig",
               "verworfen_pro_stunde": stufe0.get("verworfen_pro_stunde", 0),
               "regeln": [{"titel": r["titel"], "pro_stunde": r.get("pro_stunde", 0)}
                          for r in stufe0.get("regeln", [])]})
    return {"felder": {q: dict(c) for q, c in felder.items()},
            "nicht_zugeordnet": dict(nicht_zugeordnet), "laeufe": laeufe,
            "definition_v": definition_v, "historie_aktiv": historie_aktiv,
            # an der Quelle (Graylog-Pipeline) verworfen — sieht keine Auswertung; None = nie gemessen
            "stufe0": s0}


# ── Abfragen ───────────────────────────────────────────────────────────────────

async def _zeilen(db, seit: datetime) -> list[tuple[str, datetime, dict]]:
    from sqlalchemy import select

    from app.models.log_watch import RasterLauf
    rows = (await db.execute(select(RasterLauf).where(RasterLauf.fenster_start >= seit))).scalars()
    return [(r.quelle, r.fenster_start, r.werte) for r in rows]


def befunde(hits: list[dict], pruefung: dict, ki_an: bool, stunden: int) -> dict:
    """KI-Befunde als Objekte: Titel, Relevanz, Hypothesen, geprüfte Belege, Trichter-Schritte.
    Kein Befund ist ein benannter Zustand mit dem Stand der Prüfung, keine leere Kachel."""
    items = []
    for h in hits:
        s = h.get("_source") or {}
        pv = s.get("ai_provenance") or {}
        items.append({
            "id": h["_id"], "zeit": s.get("zeit"), "host": s.get("host"), "relevanz": s.get("relevanz"),
            "titel": pv.get("titel") or (s.get("text") or "").split("\n")[0][:120],
            "befund": s.get("ai_befund") or "", "hashtags": s.get("hashtags") or [],
            # Form seit 07.11.: kern + fakten + folge_von (ältere Befunde: nur `befund`, die Kachel teilt ihn)
            "kern": pv.get("kern") or "", "fakten": pv.get("fakten") or [], "folge_von": pv.get("folge_von") or [],
            "kriterien": s.get("kriterien") or [], "hypothesen": pv.get("hypothesen") or [],
            "belege": pv.get("belege") or [], "verworfen": len(pv.get("verworfen") or []),
            "schritte": [{"werkzeug": x.get("werkzeug"), "filter": (x.get("argumente") or {}).get("filter"),
                          "gesamt": x.get("gesamt")} for x in pv.get("schritte") or []],
            "modell": pv.get("modell") or "",
        })
    warten = pruefung.get("gesamt", 0)
    return {"state": "ok" if items else "empty", "befunde": items, "zeitraum_h": stunden,
            "pruefung": {"wartend": warten, "hosts": pruefung.get("hosts", [])[:5], "ki_aktiv": ki_an},
            **({} if items else {"grund": (
                f"Kein KI-Befund in {stunden} h. " + (
                    f"{warten} Nachrichten (Relevanz 3 oder neues Muster) warten auf Analyse"
                    + ("" if ki_an else " — KI-Trichter ist ausgeschaltet (log_watch.ki_aktiv)")
                    if warten else "Die Prüfung ist nicht wahr: nichts mit Relevanz 3 oder neuem Muster."))})}


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
        if typ == "lw_netzplan":
            from app.services.log_watch import suche
            n = await suche.netzplan(str(cfg.get("filter") or ""), stunden=stunden,
                                     knoten=int(cfg.get("knoten") or 15), objekte=(await aktuelle(db)).objekte)
            if "fehler" in n:
                return {"state": "unavailable", "error": f"Filter: {n['fehler']}"}
            return {**n, "state": "ok" if n["knoten"] else "empty", "zeitraum_h": stunden}
        from app.core.opensearch import get_opensearch
        from app.services.log_watch import trichter
        from app.services.settings import get_setting
        res = await get_opensearch().search(index=f"{historie.PRAEFIX}*", ignore_unavailable=True, body={
            "size": min(int(cfg.get("limit") or 8), 30),
            "query": {"bool": {"filter": [{"term": {"quelle": "ki"}},
                                          {"range": {"zeit": {"gte": seit.isoformat()}}}]}},
            "sort": [{"relevanz": "desc"}, {"zeit": "desc"}], "_source": {"excludes": ["vektor"]}})
        ki_an = (await get_setting(db, trichter.SCHALTER) or "").lower() in ("1", "true", "ja")
        return befunde(res["hits"]["hits"], await trichter.kandidaten(), ki_an, stunden)
    if typ == "lw_kriterien":
        d = await aktuelle(db)
        return {**kriterien(await _zeilen(db, seit), d.kriterien, stunden), "definition_v": d.version}
    if typ == "lw_ki":
        from app.services.log_watch import trichter
        from app.services.settings import get_setting
        an = (await get_setting(db, trichter.SCHALTER) or "").lower() in ("1", "true", "ja")
        from app.services.log_watch import bekannt
        b = await bekannt.liste(limit=0)
        return {**ki(await _zeilen(db, seit), stunden), "ki_aktiv": an,
                "kapazitaet": {"agenten": trichter.AGENTEN, "budget_s": trichter.AGENT_BUDGET_S},
                "bekannt": {k: b[k] for k in ("gesamt", "je_status", "wiederholungen")}}
    if typ == "lw_volumen":
        return volumen(await _zeilen(db, seit), stunden)
    if typ == "lw_hashtags":
        zeilen = await _zeilen(db, seit - timedelta(hours=stunden))
        h = hashtags(zeilen, seit, int(cfg.get("limit") or 15))
        from app.services.log_watch.suche import namen   # lesbare Namen (regel:11307 → Beschreibung …)
        n = await namen([x["tag"] for x in h["hashtags"]])
        for x in h["hashtags"]:
            if n.get(x["tag"]):
                x["name"] = n[x["tag"]]
        return {**h, "zeitraum_h": stunden}
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
        s0 = (await db.execute(select(RasterLauf).where(RasterLauf.quelle == "stufe0")
                               .order_by(RasterLauf.fenster_start.desc()).limit(1))).scalar_one_or_none()
        d = await aktuelle(db)
        return korpus(katalog, await _zeilen(db, jetzt - timedelta(hours=1)), letzte, d.version,
                      await historie.aktiv(db), jetzt, s0.werte if s0 else None)
    return {"state": "unavailable", "error": f"unbekannter Widget-Typ {typ}"}
