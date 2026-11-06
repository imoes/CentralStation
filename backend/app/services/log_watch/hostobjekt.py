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

#: so viele Nachrichten bzw. Zeichen je Nachricht kommen in die Lage (zitierbar: Teilstring)
LAGE_MAX = 15
TEXT_MAX = 400
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


async def baue(host: str, stunden: float, objekte: dict | None = None) -> dict:
    """Das Host-Objekt aus einer Suche (Lage), dem Netzplan, dem Hostverzeichnis und dem Gedächtnis."""
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch import gespraech, suche
    from app.services.log_watch.hosts import verzeichnis

    async with AsyncSessionLocal() as db:
        verz = await verzeichnis(db)
    e, _k, grund = verz.zuordnen(host)
    felder = ({"name": e.name, "ort": e.ort or None, "kritikalitaet": e.kritikalitaet or None,
               "netz": e.netz or None, "netzbereich": e.netzbereich or None, "hostgruppen": e.hostgruppen}
              if e else {"name": host, "in_checkmk": False, "grund": grund})
    lage = await suche.suche(f"host:{host} !quelle:(ki OR konsole)", stunden=stunden, limit=LAGE_MAX,
                             objekte=objekte)
    netz = await suche.netzplan(f"host:{host} !quelle:(ki OR konsole)", stunden=stunden, knoten=12,
                                objekte=objekte)
    befunde = await suche.suche(f"host:{host} quelle:ki", stunden=24 * 7, limit=5, objekte=objekte,
                                sortierung="zeit")
    sitzungen = await gespraech.lade(f"host:{host}", tage=7, sitzungen=3)
    return {
        "host": host, "felder": felder,
        "lage": {"zeitraum_h": stunden, "gesamt": lage.get("gesamt", 0),
                 "nachrichten": kuerze_lage(lage.get("treffer", [])),
                 "facetten": kuerze_facetten(lage.get("facetten", {}))},
        "nachbarschaft": [f"{k['a']} — {k['b']} ({k['anzahl']})" for k in netz.get("kanten", [])
                          if f"host:{host}" in (k["a"], k["b"])][:10],
        "gedaechtnis": kuerze_gedaechtnis(befunde.get("treffer", []), sitzungen),
    }
