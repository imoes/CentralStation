"""Host-Objekt: was der Worker sucht, bevor die KI arbeitet — ihr Prompt ist dieses Suchergebnis.

Vorher begann jede Analyse mit denselben Werkzeugschritten (lw_wissen → lw_suche/lw_facetten;
gemessen an 20 Befunden: 19,9 s von 62,2 s Modellzeit). Jetzt erledigt das der Worker mit
derselben Suche, und die KI bekommt das Ergebnis als Objekt:

  felder         Stammdaten aus CheckMK (Ort, Kritikalität, Netz, Hostgruppen) — oder benannt
                 „nicht in CheckMK“ mit Grund
  lage           relevante Nachrichten im Zeitraum (IDs + Text zum Zitieren), Facetten
  nachbarschaft  Netzplan-Kanten des Hosts (gemeinsam an derselben Nachricht, keine Ursache)
  gedaechtnis    frühere Befunde (Titel, Hypothesen mit Status) und das Gesprächsprotokoll
                 der letzten Sitzungen — geladen per Suche host:<name>; dazu gehören auch
                 Konsolen-Sitzungen mit Bezug (herkunft "konsole", siehe konsole.py)
"""
from __future__ import annotations

#: so viele Nachrichten bzw. Zeichen je Nachricht kommen in die Lage (zitierbar: Teilstring).
#: Gemessen 06.11. (erste Läufe): je Host eine eigene Lage machte Prompts von 6 000–20 000 Zeichen.
LAGE_MAX = 12
TEXT_MAX = 300
FACETTEN = ("thema", "muster", "regel", "regelgruppe", "mitre", "dienst", "signal", "quelle", "zustand")
JE_FACETTE = 5
#: je Treffer höchstens so viele Gründe bzw. Hashtags (gemessen 07.11.: im Mittel 106 bzw. 91 Zeichen
#: je Treffer, mehr als der halbe Text; die Facetten fassen die Hashtags ohnehin zusammen)
JE_TREFFER = 2
JE_TREFFER_TAGS = 5


def kuerze_lage(treffer: list[dict]) -> list[dict]:
    """Treffer → Lage-Einträge mit ID, Zeit, Quelle, Relevanz, Gründen und gekürztem Text."""
    return [{"id": t["id"], "zeit": str(t.get("zeit") or "")[:19], "quelle": t.get("quelle"), "host": t.get("host"),
             "relevanz": t.get("relevanz"),
             "gruende": (t.get("gruende") or [])[:JE_TREFFER],
             "hashtags": [h for h in t.get("hashtags") or [] if h.split(":")[0] in FACETTEN][:JE_TREFFER_TAGS],
             "text": (t.get("text") or "")[:TEXT_MAX]} for t in treffer[:LAGE_MAX]]


def kuerze_facetten(facetten: dict, arten: tuple[str, ...] | None = None) -> dict:
    """Top-Werte je Art als „wert (anzahl)“; `arten=None` heißt die Arten der Lage (FACETTEN)."""
    return {art: [f"{x['wert']} ({x['anzahl']})" for x in facetten.get(art, [])[:JE_FACETTE]]
            for art in (arten or FACETTEN) if facetten.get(art)}


#: so viele Treffer zeigt eine Werkzeugantwort im Trichter (gemessen 07.11. an 40 echten lw_suche-Aufrufen:
#: Median 10 246 Zeichen roh, 29 von 40 über der 6 000-Zeichen-Grenze — blind abgeschnitten, die
#: Facetten dahinter sah die KI nie). Mit 6 Treffern: Median 3 523, höchstens 5 316 Zeichen.
JE_ANTWORT = 6


def kuerze_antwort(werkzeug: str, erg: dict) -> dict:
    """Werkzeugantwort für die KI in derselben Ansicht wie die Lage im Prompt: Treffer mit gekürztem,
    aber zitierbarem Text (Teilstring des Originals), Facetten als Top-Werte. Was fehlt, ist benannt."""
    if not isinstance(erg, dict) or "fehler" in erg:
        return erg
    kopf = {k: erg[k] for k in ("filter", "zeitraum", "gesamt") if k in erg}
    if werkzeug == "lw_suche":
        treffer = kuerze_lage(erg.get("treffer") or [])[:JE_ANTWORT]
        return {**kopf, "gezeigt": len(treffer), "treffer": treffer,
                "facetten": kuerze_facetten(erg.get("facetten") or {}),
                "hinweis": f"je Treffer höchstens {TEXT_MAX} Zeichen Text, höchstens {JE_ANTWORT} Treffer; "
                           "vollständig: lw_nachricht(id)"}
    if werkzeug == "lw_facetten":
        f = erg.get("facetten") or {}
        return {**kopf, "facetten": kuerze_facetten(f, tuple(f)),
                "hinweis": f"je Art die {JE_FACETTE} häufigsten Werte"}
    if werkzeug == "lw_gespraech":
        return {"filter": erg.get("filter"),
                "sitzungen": kuerze_gedaechtnis([], erg.get("sitzungen") or [])["gespraeche"]}
    if werkzeug == "get_checkmk_host":
        services = erg.get("services") or []
        nicht_ok = [{"service": x.get("name"), "zustand": x.get("state_label"),
                     "ausgabe": (x.get("summary") or "")[:TEXT_MAX]} for x in services if x.get("state")]
        return {"host": erg.get("hostname"), "site": erg.get("site"), "nicht_ok": nicht_ok[:JE_ANTWORT * 2],
                "nicht_ok_gesamt": len(nicht_ok), "ok": len(services) - len(nicht_ok),
                "metriken_2h": [f"{m.get('service')} {m.get('metric')}: {m.get('current')}{m.get('unit') or ''} "
                                f"(min {m.get('min')}, max {m.get('max')}, {m.get('trend')})"
                                for m in erg.get("performance") or []]}
    if werkzeug == "search_tickets":
        return {"gesucht_in": erg.get("searched"), "gesamt": erg.get("total_found", 0),
                "tickets": [{"key": t.get("key"), "titel": t.get("summary"), "status": t.get("status"),
                             "aktualisiert": str(t.get("updated") or "")[:10], "instanz": t.get("instance"),
                             "stelle": (t.get("match") or "")[:TEXT_MAX]} for t in erg.get("issues") or []],
                **({"hinweise": erg["notes"]} if erg.get("notes") else {})}
    if werkzeug == "web_seite":
        return {**{k: erg[k] for k in ("url", "laenge", "gekuerzt") if k in erg}, "text": (erg.get("text") or "")[:TEXT_MAX * 10]}
    if werkzeug == "search_knowledge_base":
        return {"gesamt": erg.get("count", 0),
                "artikel": [{"titel": a.get("title"), "url": a.get("url"),
                             "auszug": (a.get("excerpt") or "")[:TEXT_MAX * 2]} for a in erg.get("results") or []]}
    return erg


def kuerze_gedaechtnis(befunde: list[dict], sitzungen: list[dict]) -> dict:
    """Frühere Befunde und die KI-Züge früherer Sitzungen (ohne die großen Werkzeugantworten)."""
    return {
        "befunde": [{"id": b["id"], "zeit": b.get("zeit"), "relevanz": b.get("relevanz"),
                     "befund": (b.get("ai_befund") or b.get("text") or "")[:600]} for b in befunde[:5]],
        "gespraeche": [{"sitzung": s["sitzung"],
                        "herkunft": "konsole" if any("quelle:konsole" in (z.get("hashtags") or [])
                                                     for z in s["zuege"]) else "ki-analyse",
                        "bezug": (s["zuege"] or [{}])[0].get("fall"), "zuege": [
            {"rolle": z["rolle"], **({"werkzeug": z["werkzeug"]} if z.get("werkzeug") else {}),
             "text": (z.get("text") or "")[:300]}
            for z in s["zuege"] if z["rolle"] in ("ki", "befund", "mensch")]} for s in sitzungen],
    }


async def fall_lage(start: str, stunden: float, objekte: dict | None = None) -> dict:
    """Die Lage des FALLS: dieselbe Suche, mit der die KI sonst begann (Startfilter, Facetten,
    frühere Befunde dazu). Gemessen 06.11.: mit einer Lage je Host statt je Fall rief die KI bei
    3 von 4 Muster-Fällen trotzdem lw_wissen/lw_suche/lw_facetten mit dem Muster auf."""
    from app.services.log_watch import suche
    lage = await suche.suche(f"{start} !quelle:(ki OR konsole)", stunden=stunden, limit=LAGE_MAX, objekte=objekte)
    frueher = await suche.suche(f"{start} quelle:ki", stunden=24 * 7, limit=3, objekte=objekte, sortierung="zeit")
    return {"startfilter": start, "zeitraum_h": stunden, "gesamt": lage.get("gesamt", 0),
            "nachrichten": kuerze_lage(lage.get("treffer", [])),
            "facetten": kuerze_facetten(lage.get("facetten", {})),
            "fruehere_befunde_zum_startfilter": kuerze_gedaechtnis(frueher.get("treffer", []), [])["befunde"]}


async def baue(host: str, stunden: float, objekte: dict | None = None) -> dict:
    """Das Host-Objekt: Felder (Hostverzeichnis), Nachbarschaft (Netzplan), Gedächtnis (frühere
    Befunde und Gespräche). Die Lage steht einmal am Fall (fall_lage), nicht je Host."""
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch import gespraech, suche
    from app.services.log_watch.hosts import verzeichnis

    async with AsyncSessionLocal() as db:
        verz = await verzeichnis(db)
    e, _k, grund = verz.zuordnen(host)
    felder = ({"name": e.name, "ort": e.ort or None, "kritikalitaet": e.kritikalitaet or None,
               "netz": e.netz or None, "netzbereich": e.netzbereich or None, "hostgruppen": e.hostgruppen}
              if e else {"name": host, "in_checkmk": False, "grund": grund})
    netz = await suche.netzplan(f"host:{host} !quelle:(ki OR konsole)", stunden=stunden, knoten=12,
                                objekte=objekte)
    befunde = await suche.suche(f"host:{host} quelle:ki", stunden=24 * 7, limit=5, objekte=objekte,
                                sortierung="zeit")
    sitzungen = await gespraech.lade(f"host:{host}", tage=7, sitzungen=3)
    return {
        "host": host, "felder": felder,
        "nachbarschaft": [f"{k['a']} — {k['b']} ({k['anzahl']})" for k in netz.get("kanten", [])
                          if f"host:{host}" in (k["a"], k["b"])][:10],
        "gedaechtnis": kuerze_gedaechtnis(befunde.get("treffer", []), sitzungen),
    }
