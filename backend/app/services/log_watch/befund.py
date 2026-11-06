"""Befund der KI = ein Dokument `quelle:ki` in der Historie (Plan „Befunde der KI sind Nachrichten“).

Die KI schreibt nicht selbst: sie reicht Titel, Befund, Relevanz, Hypothesen und Belege ein,
der Code prüft und schreibt. Geprüft wird:
  - jeder Beleg ist ein Dokument der Historie, und sein Zitat steht wörtlich in dessen Text;
  - ohne mindestens einen geprüften Beleg gibt es keinen Befund (Grund geht an die KI zurück);
  - Hashtags stammen nur aus den Belegen (Identität: kein Begriff, den keine Nachricht trägt),
    dazu `quelle:ki` und `befund:<id>`;
  - Hypothesen tragen einen Status aus HYPOTHESE_STATUS.
Die Belege bekommen den Hashtag `befund:<id>` — derselbe Name wie am Befund (Identität): der Filter
`befund:<id>` findet Befund und Belege, im Netzplan ist der Befund ein Knoten mit Kanten zu ihnen.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.log_watch.definition import NICHT_RELEVANT, RELEVANT
from app.services.log_watch.historie import PRAEFIX, index_name

HYPOTHESE_STATUS = ("offen", "gestuetzt", "widerlegt")
#: Zitate kürzer als das belegen nichts (ein Wort passt fast überall)
ZITAT_MIN = 8
#: Reste der Werkzeugaufruf-Syntax des Modells im Text (gemessen 06.11.: qwen hängte
#: „</parameter>\n<parameter=relevanz>\n3“ an den Befund) — kein Inhalt, sondern ein Formatfehler
_SYNTAX_REST = re.compile(r"</?parameter\b|<parameter=|</?tool_call>|<function=|</function>")


class BefundAbgelehnt(ValueError):
    """Befund nicht schreibbar — der Grund geht an die KI zurück, sie kann nachbessern."""


@dataclass
class Pruefung:
    gueltig: list[dict] = field(default_factory=list)       # [{"id","zitat","host","hashtags",…}]
    verworfen: list[dict] = field(default_factory=list)     # [{"id","grund"}]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def pruefe_belege(belege: list[dict], dokumente: dict[str, dict]) -> Pruefung:
    """Belege der KI gegen die gelesenen Dokumente prüfen. Rein — testbar."""
    p = Pruefung()
    gesehen = set()
    for b in belege or []:
        bid, zitat = str(b.get("id") or ""), str(b.get("zitat") or "")
        if (bid, _norm(zitat)) in gesehen:          # dasselbe Zitat zweimal: einmal genügt
            continue
        gesehen.add((bid, _norm(zitat)))
        d = dokumente.get(bid)
        if d is None:
            p.verworfen.append({"id": bid, "grund": "kein Dokument der Historie mit dieser ID"})
        elif d.get("quelle") == "ki":
            p.verworfen.append({"id": bid, "grund": "ein Befund belegt keinen Befund (Selbstbestätigung)"})
        elif len(_norm(zitat)) < ZITAT_MIN:
            p.verworfen.append({"id": bid, "grund": f"Zitat fehlt oder kürzer als {ZITAT_MIN} Zeichen"})
        elif _norm(zitat) not in _norm(d.get("text", "")):
            p.verworfen.append({"id": bid, "grund": "Zitat steht nicht wörtlich im Text der Nachricht"})
        else:
            p.gueltig.append({"id": bid, "zitat": zitat, "host": d.get("host"),
                              "quelle": d.get("quelle"), "zeit": d.get("zeit"),
                              "hashtags": d.get("hashtags") or [], "kriterien": d.get("kriterien") or [],
                              "index": d.get("_index")})
    return p


def baue(eingabe: dict, pruefung: Pruefung, jetzt: datetime, modell: str = "",
         schritte: list | None = None, start: str = "") -> tuple[str, dict]:
    """(Dokument-ID, Dokument) des Befunds. Wirft BefundAbgelehnt mit Grund."""
    titel = str(eingabe.get("titel") or "").strip()
    text = str(eingabe.get("befund") or "").strip()
    if not titel or not text:
        raise BefundAbgelehnt("Titel und Befund sind Pflicht")
    for name, wert in (("Titel", titel), ("Befund", text)):
        if _SYNTAX_REST.search(wert):
            raise BefundAbgelehnt(f"{name} enthält Werkzeug-Syntax ({_SYNTAX_REST.search(wert).group(0)!r}) "
                                  f"— nur Klartext, jedes Argument einzeln")
    try:
        relevanz = int(eingabe.get("relevanz"))
    except (TypeError, ValueError):
        raise BefundAbgelehnt("Relevanz muss eine Zahl 0–3 sein") from None
    if not 0 <= relevanz <= 3:
        raise BefundAbgelehnt("Relevanz muss 0–3 sein")
    if not pruefung.gueltig:
        gruende = "; ".join(f"{v['id']}: {v['grund']}" for v in pruefung.verworfen) or "keine Belege"
        raise BefundAbgelehnt(f"kein geprüfter Beleg — {gruende}")
    hypothesen = []
    for h in eingabe.get("hypothesen") or []:
        st = str((h or {}).get("status") or "offen")
        if st not in HYPOTHESE_STATUS:
            raise BefundAbgelehnt(f"Hypothesen-Status {st!r} unbekannt (erlaubt: {', '.join(HYPOTHESE_STATUS)})")
        hypothesen.append({"text": str(h.get("text") or "")[:1000], "status": st})

    kurz = uuid.uuid4().hex[:10]
    doc_id = f"ki:{kurz}"
    tags_belege = Counter(t for b in pruefung.gueltig for t in b["hashtags"] if not t.startswith("befund:"))
    gewuenscht = [t for t in (eingabe.get("hashtags") or []) if isinstance(t, str)]
    #: von der KI genannte Hashtags gelten nur, wenn ein Beleg sie trägt
    hashtags = sorted({t for t in gewuenscht if t in tags_belege} or set(tags_belege))
    hashtags = [t for t in hashtags if not t.startswith("quelle:")] + ["quelle:ki", f"befund:{kurz}"]
    hosts = Counter(b["host"] for b in pruefung.gueltig if b["host"])
    belegt = {b["id"]: b for b in pruefung.gueltig}             # je Dokument einmal
    return doc_id, {
        "zeit": jetzt.astimezone(timezone.utc).isoformat(),
        "quelle": "ki", "herkunft": "ki", "host": hosts.most_common(1)[0][0] if hosts else "",
        "schluessel": f"befund:{kurz}", "relevanz": relevanz, "ai_relevanz": relevanz,
        "urteil": RELEVANT if relevanz >= 2 else NICHT_RELEVANT,
        "kriterien": sorted({k for b in belegt.values() for k in b["kriterien"]}),
        "hashtags": hashtags, "ai_hashtags": hashtags,
        "text": f"{titel}\n\n{text}"[:20000], "ai_befund": text[:20000],
        "gruende": [f"Befund der KI aus {len(pruefung.gueltig)} geprüften Zitaten in "
                    f"{len(belegt)} Nachrichten"],
        "gruppe_anzahl": len(belegt),
        "ai_provenance": {
            "titel": titel, "modell": modell, "hypothesen": hypothesen,
            # Startfilter der Analyse: alles, was er bis jetzt traf, gilt als gesehen (trichter.offene)
            "start": start,
            "belege": [{k: b[k] for k in ("id", "zitat", "host", "quelle", "zeit")} for b in pruefung.gueltig],
            "verworfen": pruefung.verworfen, "schritte": schritte or [],
        },
    }


async def lies_dokumente(ids: list[str]) -> dict[str, dict]:
    """Dokumente der Historie über ihre IDs (Index unbekannt → ids-Query über cs-logs-*)."""
    from app.core.opensearch import get_opensearch
    if not ids:
        return {}
    res = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": len(ids), "query": {"ids": {"values": ids}},
        "_source": ["text", "host", "quelle", "zeit", "hashtags", "kriterien"]})
    return {h["_id"]: {**h["_source"], "_index": h["_index"]} for h in res["hits"]["hits"]}


async def schreibe_befund(eingabe: dict, modell: str = "", schritte: list | None = None,
                          jetzt: datetime | None = None, start: str = "") -> dict:
    """Prüfen, einbetten, schreiben, Belege verknüpfen. Ergebnis {id, belege, verworfen} oder
    BefundAbgelehnt."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.embed import EmbeddingFehler, einbetten

    jetzt = jetzt or datetime.now(timezone.utc)
    belege = [b for b in (eingabe.get("belege") or []) if isinstance(b, dict)]
    pruefung = pruefe_belege(belege, await lies_dokumente([str(b.get("id")) for b in belege]))
    doc_id, doc = baue(eingabe, pruefung, jetzt, modell, schritte, start)
    try:
        (doc["vektor"],) = await einbetten([doc["text"]])
    except EmbeddingFehler:
        pass                                            # nachbetten() holt es nach
    os_client = get_opensearch()
    await os_client.index(index=index_name(jetzt), id=doc_id, body=doc, refresh=True)
    body: list[dict] = []
    for b in {b["id"]: b for b in pruefung.gueltig}.values():
        body += [{"update": {"_index": b["index"], "_id": b["id"]}},
                 {"script": {"lang": "painless",
                             "source": "if (ctx._source.hashtags == null) { ctx._source.hashtags = []; }"
                                       " if (!ctx._source.hashtags.contains(params.b)) "
                                       "{ ctx._source.hashtags.add(params.b); }",
                             "params": {"b": doc["schluessel"]}}}]
    if body:
        await os_client.bulk(body=body, refresh=True)
    return {"id": doc_id, "belege": len(pruefung.gueltig), "verworfen": pruefung.verworfen,
            "hashtags": doc["hashtags"]}
