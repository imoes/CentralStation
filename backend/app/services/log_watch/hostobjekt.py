"""Host-Objekt: was der Worker sucht, bevor die KI arbeitet — ihr Prompt ist dieses Suchergebnis.

Vorher begann jede Analyse mit denselben Werkzeugschritten (lw_wissen → lw_suche/lw_facetten;
gemessen an 20 Befunden: 19,9 s von 62,2 s Modellzeit). Jetzt erledigt das der Worker mit
derselben Suche, und die KI bekommt das Ergebnis als Objekt:

  felder         Stammdaten aus CheckMK (Ort, Kritikalität, Netz, Hostgruppen) — oder benannt
                 „nicht in CheckMK“ mit Grund
  lage           relevante Nachrichten im Zeitraum (IDs + Text zum Zitieren), Facetten
  nachbarschaft  Netzplan-Kanten des Hosts (gemeinsam an derselben Nachricht, keine Ursache)
  gedaechtnis    frühere Befunde (Titel, Hypothesen mit Status) und das Gesprächsprotokoll
                 der letzten Sitzungen — geladen per Suche host:<name>
"""
from __future__ import annotations

#: so viele Nachrichten bzw. Zeichen je Nachricht kommen in die Lage (zitierbar: Teilstring).
#: Gemessen 06.11. (erste Läufe): je Host eine eigene Lage machte Prompts von 6 000–20 000 Zeichen.
LAGE_MAX = 12
TEXT_MAX = 300
FACETTEN = ("thema", "muster", "regel", "regelgruppe", "mitre", "dienst", "signal", "quelle", "zustand")
JE_FACETTE = 5


def kuerze_lage(treffer: list[dict]) -> list[dict]:
    """Treffer → Lage-Einträge mit ID, Zeit, Quelle, Relevanz, Gründen und gekürztem Text."""
    return [{"id": t["id"], "zeit": t.get("zeit"), "quelle": t.get("quelle"), "relevanz": t.get("relevanz"),
             "gruende": (t.get("gruende") or [])[:3],
             "hashtags": [h for h in t.get("hashtags") or [] if h.split(":")[0] in FACETTEN],
             "text": (t.get("text") or "")[:TEXT_MAX]} for t in treffer[:LAGE_MAX]]


def kuerze_facetten(facetten: dict) -> dict:
    return {art: [f"{x['wert']} ({x['anzahl']})" for x in facetten.get(art, [])[:JE_FACETTE]]
            for art in FACETTEN if facetten.get(art)}


def kuerze_gedaechtnis(befunde: list[dict], sitzungen: list[dict]) -> dict:
    """Frühere Befunde und die KI-Züge früherer Sitzungen (ohne die großen Werkzeugantworten)."""
    return {
        "befunde": [{"id": b["id"], "zeit": b.get("zeit"), "relevanz": b.get("relevanz"),
                     "befund": (b.get("ai_befund") or b.get("text") or "")[:600]} for b in befunde[:5]],
        "gespraeche": [{"sitzung": s["sitzung"], "zuege": [
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
