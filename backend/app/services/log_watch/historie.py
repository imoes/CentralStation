"""Historie `cs-logs-*`: nur relevant getaggte Nachrichten (Relevanz ≥ 2), mit allen
Originalfeldern, Hashtags, Gründen und Vektor.

Ein Dokument = eine Nachricht der Quelle. Die Originalfelder liegen unter `roh`
(`flat_object`: jedes Feld durchsuchbar, ohne dass 1 191 Graylog-Felder das Mapping
sprengen). Felder, die Code, API und Oberfläche teilen, heißen überall gleich:
`zeit, quelle, host, schluessel, relevanz, urteil, herkunft, gruende, hashtags, text`.

Selektoren (Filter der Suche, Facetten, Netzplan): `quelle, host, hashtags, relevanz, urteil,
herkunft, zeit`. `urteil` hat genau zwei Werte, relevant | nicht_relevant; eine als nicht
relevant beurteilte Nachricht bleibt als Gegenbeispiel stehen — daran lernt das System.

Spätere KI-Felder (`ai_*`, `befund_ids`) sind im Mapping schon vorgesehen; ein Befund
der KI ist selbst ein Dokument mit `quelle: ki` (Plan „Befunde der KI sind Nachrichten“).

Geschrieben wird nur, wenn der Schalter `log_watch.historie_aktiv` gesetzt ist — bis der
Trockenlauf belegt hat, wie viel pro Tag anfällt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services.log_watch.embed import DIM
from app.services.log_watch.raster import SPEICHERN_AB, Treffer

log = logging.getLogger(__name__)

PRAEFIX = "cs-logs-"
VORLAGE = "cs-logs"
SCHALTER = "log_watch.historie_aktiv"
#: Werte des Selektors `urteil` — erschöpfend
RELEVANT, NICHT_RELEVANT = "relevant", "nicht_relevant"
URTEILE = (RELEVANT, NICHT_RELEVANT)
#: Wer geurteilt hat — ein Mensch hat Vorrang vor der KI, die KI vor der Regel
HERKUNFT = ("regel", "ki", "mensch")
#: Version der Raster-Regeln, mit der ein Dokument bewertet wurde (Marker wie in recherche-stack)
RASTER_V = 1

MAPPING = {
    "dynamic": "strict",
    "properties": {
        "zeit": {"type": "date"},
        "quelle": {"type": "keyword"},
        "host": {"type": "keyword"},
        "host_bekannt": {"type": "boolean"},
        "schluessel": {"type": "keyword"},
        "relevanz": {"type": "byte"},
        # Selektor: genau zwei Werte. „Nur vom Skript beurteilt“ ist kein dritter Wert,
        # sondern steht in `herkunft` (regel) — so ist jeder Fall klassifiziert.
        "urteil": {"type": "keyword"},                     # relevant | nicht_relevant
        "herkunft": {"type": "keyword"},                   # regel | ki | mensch (wer urteilte)
        "gruende": {"type": "text"},
        "hashtags": {"type": "keyword"},
        "text": {"type": "text", "fields": {"exakt": {"type": "keyword", "ignore_above": 512}}},
        "gruppe_anzahl": {"type": "integer"},              # Nachrichten der Gruppe im Fenster
        "raster_v": {"type": "short"},
        "roh": {"type": "flat_object"},
        "vektor": {"type": "knn_vector", "dimension": DIM,
                   "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"}},
        # KI (Plan Block D): an belegenden Nachrichten und an Befund-Dokumenten
        "ai_relevanz": {"type": "byte"},
        "ai_hashtags": {"type": "keyword"},
        "ai_befund": {"type": "text"},
        "befund_ids": {"type": "keyword"},
        "ai_provenance": {"type": "object", "enabled": False},
    },
}
VORLAGE_BODY = {
    "index_patterns": [f"{PRAEFIX}*"],
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0, "index.knn": True},
        "mappings": MAPPING,
    },
    "priority": 100,
}


def index_name(zeit: datetime) -> str:
    return f"{PRAEFIX}{zeit.astimezone(timezone.utc):%Y.%m.%d}"


def dokument(t: Treffer, quell_id: str, zeit: datetime, text: str, roh: dict) -> tuple[str, dict]:
    """(Dokument-ID, Dokument) für eine Nachricht eines Treffers. Rein — testbar.

    Die ID ist `<quelle>:<ID in der Quelle>`: ein zweiter Lauf überschreibt, statt zu
    verdoppeln.
    """
    if t.relevanz < SPEICHERN_AB:
        raise ValueError(f"Relevanz {t.relevanz} gehört nicht in die Historie")
    return f"{t.quelle}:{quell_id}", {
        "zeit": zeit.astimezone(timezone.utc).isoformat(),
        "quelle": t.quelle, "host": t.host, "host_bekannt": t.host_bekannt,
        "schluessel": t.schluessel, "relevanz": t.relevanz,
        "urteil": RELEVANT, "herkunft": "regel",
        "gruende": t.gruende, "hashtags": t.hashtags, "text": (text or t.beispiel)[:20000],
        "gruppe_anzahl": t.anzahl, "raster_v": RASTER_V, "roh": _flach(roh),
    }


def _flach(roh: dict) -> dict:
    """flat_object nimmt nur Objekte mit Text-/Zahl-Blättern; Listen werden zu Text."""
    out = {}
    for k, v in (roh or {}).items():
        if v is None:
            continue
        if isinstance(v, dict):
            out[k] = _flach(v)
        elif isinstance(v, list):
            out[k] = ", ".join(str(x) for x in v)[:2000]
        else:
            out[k] = v if isinstance(v, (int, float, bool)) else str(v)[:5000]
    return out


async def aktiv(db) -> bool:
    from app.services.settings import get_setting
    return (await get_setting(db, SCHALTER) or "").lower() in ("1", "true", "ja")


async def stelle_bereit() -> None:
    """Index-Vorlage anlegen bzw. aktualisieren (wirkt auf jeden neuen Tagesindex)."""
    from app.core.opensearch import get_opensearch
    os_client = get_opensearch()
    await os_client.indices.put_index_template(name=VORLAGE, body=VORLAGE_BODY)


async def schreibe(docs: list[tuple[str, datetime, dict]]) -> dict:
    """Dokumente (ID, Zeit, Body) einbetten und per _bulk schreiben.

    Ein Embedding-Ausfall verhindert das Schreiben nicht: die Dokumente gehen ohne Vektor
    in die Historie und tragen das als benannten Zustand (`vektor_fehlt` im Ergebnis);
    ein Nachlauf bettet sie später ein.
    """
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.embed import EmbeddingFehler, einbetten

    if not docs:
        return {"geschrieben": 0, "fehler": 0, "vektor_fehlt": 0}
    vektor_fehlt = 0
    try:
        vektoren = await einbetten([d["text"] for _, _, d in docs])
        for (_, _, d), v in zip(docs, vektoren):
            d["vektor"] = v
    except EmbeddingFehler as e:
        log.warning("Historie: Embedding fehlgeschlagen, schreibe ohne Vektor: %s", e)
        vektor_fehlt = len(docs)
    body: list[dict] = []
    for doc_id, zeit, d in docs:
        body += [{"index": {"_index": index_name(zeit), "_id": doc_id}}, d]
    res = await get_opensearch().bulk(body=body)
    fehler = [i for i in res.get("items", []) if (i.get("index") or {}).get("error")]
    if fehler:
        log.warning("Historie: %d von %d Dokumenten abgelehnt, erstes: %s", len(fehler),
                    len(docs), str(fehler[0]["index"]["error"])[:300])
    return {"geschrieben": len(docs) - len(fehler), "fehler": len(fehler),
            "vektor_fehlt": vektor_fehlt}
