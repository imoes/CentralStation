"""KI fragt nach — wenn sich eine Behebung oder ein Urteil nicht automatisch erkennen lässt.

Nutzer 12.11.2026 (externe Analyse F6 „kein Lernen aus Behebungen, keine Gold-Labels“): „das soll automatisch
funktionieren oder die ki soll nachfragen“ — „in der mail und in der konsole“.

Wann: ein Regel-Incident ist in den letzten 24 h geschlossen worden, und für seine Episode ist keine Maßnahme bekannt
(kein Ticket, kein Lösungs-Eintrag, keine beobachtete Maßnahme am Host im Zeitraum, keine frühere Antwort).
Höchstens MAX_JE_TAG Fragen am Tag, die größten Incidents (Nachrichten) zuerst.

Wie: dieselbe Frage zweimal zugestellt — als Mail mit Ein-Klick-Links (signiert, kein Login; Empfänger nach
Host-Scope wie die Sofort-Mail) und in der Konsole (MCP ki_fragen_offen / ki_frage_beantworten; die Konsolen-KI stellt
sie zu Beginn einer Sitzung). Die erste Antwort gilt; jede weitere wird mit Grund abgelehnt.

Wirkung einer Antwort, erschöpfend (WAHL):
  neustart · konfiguration · ticket   Maßnahme der Episode (Lösungswege) + Menschen-Urteil „relevant“ für die
                                      Nachrichten des Incidents (Gold-Label für das Relevanz-Modell)
  rauschen                            Menschen-Urteil „nicht relevant (N4 Rauschen)“
  von_selbst                          keine Maßnahme, kein Urteil — die Episode zählt zur Basisrate „von selbst“
  weiss_nicht                         schließt die Frage, nichts weiter
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

INDEX = "cs-ki-fragen"
MAX_JE_TAG = 3
STATUS = ("offen", "beantwortet")
#: Wahl → (Beschriftung, Urteil, Relevanz, Kriterium, Maßnahme)
WAHL: dict[str, tuple[str, str | None, int | None, str | None, str | None]] = {
    "neustart": ("Dienst oder Host neu gestartet", "relevant", 2, "R1", "neustart"),
    "konfiguration": ("Konfiguration geändert", "relevant", 2, "R1", "konfiguration"),
    "ticket": ("Ticket / an Fachteam übergeben", "relevant", 2, "R1", "ticket"),
    "von_selbst": ("ging von selbst weg", None, None, None, None),
    "rauschen": ("war Rauschen", "nicht_relevant", 0, "N4", None),
    "weiss_nicht": ("weiß ich nicht", None, None, None, None),
}


def token(secret: str, frage_id: str, wahl: str) -> str:
    """Signatur eines Ein-Klick-Links — wer den Link hat, kann genau diese Antwort geben, sonst nichts. Rein."""
    return hmac.new(secret.encode(), f"{frage_id}|{wahl}".encode(), hashlib.sha256).hexdigest()[:32]


def token_gueltig(secret: str, frage_id: str, wahl: str, t: str) -> bool:
    return wahl in WAHL and hmac.compare_digest(token(secret, frage_id, wahl), t or "")


def offen(incidents: list[dict], gefragt: set[str], jetzt: datetime) -> list[dict]:
    """Geschlossen in den letzten 24 h, ohne bekannte Maßnahme, noch nicht gefragt. Rein."""
    return [i for i in incidents if i["id"] not in gefragt and not i.get("massnahmen")
            and i.get("geschlossen") and jetzt - i["geschlossen"] <= timedelta(hours=24)]


def kandidaten(incidents: list[dict], gefragt: set[str], heute: int, jetzt: datetime,
               mit_empfaenger: set[str] | frozenset = frozenset()) -> list[dict]:
    """incidents: [{id, host, titel, geschlossen, nachrichten, massnahmen}] → die zu fragenden, höchstens
    MAX_JE_TAG − heute. Zuerst die mit Mail-Empfänger (Host im CheckMK-Filter eines Abonnenten), darin die größten.

    Anlass 12.11.: die erste Runde fragte drei Incidents auf Hosts außerhalb jedes Filters — 0 Mails, die Fragen
    standen nur in der Konsole. Ohne Empfänger wird weiter gefragt (Konsole), nur nachrangig. Rein."""
    frei = max(0, MAX_JE_TAG - heute)
    return sorted(offen(incidents, gefragt, jetzt),
                  key=lambda i: (i["id"] not in mit_empfaenger, -(i.get("nachrichten") or 0)))[:frei]


def frage_text(i: dict) -> str:
    return (f"Der Incident „{i['titel']}“ ({i['host']}) wurde geschlossen, ohne dass ich eine Maßnahme erkennen "
            f"konnte. Was hat geholfen?")


def mail_html(frage: dict, links: dict[str, str]) -> str:
    """Kurze Mail mit Ein-Klick-Antworten. Rein."""
    knoepfe = " ".join(f'<a href="{url}" style="display:inline-block;margin:4px;padding:6px 10px;border:1px solid #888;'
                       f'border-radius:4px;text-decoration:none">{WAHL[w][0]}</a>' for w, url in links.items())
    return (f"<p>{frage['frage']}</p><p>{knoepfe}</p>"
            f"<p style='color:#666;font-size:12px'>Ein Klick genügt — die Antwort wird als Maßnahme bzw. Urteil "
            f"gelernt (CentralStation, Frage {frage['id']}). Dieselbe Frage steht auch in der Konsole.</p>")


def wirkung(wahl: str) -> dict:
    """Was eine Antwort bewirkt. Rein."""
    if wahl not in WAHL:
        raise ValueError(f"unbekannte Antwort {wahl!r} — erlaubt: {', '.join(WAHL)}")
    _, urteil, rel, krit, mass = WAHL[wahl]
    return {"urteil": urteil, "relevanz": rel, "kriterien": [krit] if krit else [], "massnahme": mass}


# ── Daten ─────────────────────────────────────────────────────────────────────────────────────────────────
async def _os():
    from app.core.opensearch import get_opensearch
    o = get_opensearch()
    if not await o.indices.exists(index=INDEX):
        await o.indices.create(index=INDEX, body={"mappings": {"dynamic": False, "properties": {
            "erstellt": {"type": "date"}, "status": {"type": "keyword"}, "incident_id": {"type": "keyword"},
            "host": {"type": "keyword"}}}})
    return o


async def offene(host: str = "", limit: int = 10) -> list[dict]:
    o = await _os()
    f = [{"term": {"status": "offen"}}] + ([{"term": {"host": host}}] if host else [])
    r = await o.search(index=INDEX, body={"size": limit, "sort": [{"erstellt": "desc"}], "query": {"bool": {"filter": f}}})
    return [h["_source"] for h in r["hits"]["hits"]]


async def _incident_daten(db, seit: datetime) -> list[dict]:
    """Geschlossene Regel-Incidents seit `seit` mit dokumentierten Maßnahmen (Ticket, Lösungs-Eintrag, Antwort)."""
    from sqlalchemy import text
    rows = (await db.execute(text(
        "select i.id::text, i.primary_host, i.title, i.resolved_at, i.created_at, "
        "(select max((e->>'nachrichten')::int) from jsonb_array_elements(i.causal_context::jsonb) e "
        " where e->>'type' = 'error_cluster'), "
        "(select jsonb_agg(e->>'type') from jsonb_array_elements(i.causal_context::jsonb) e "
        " where e->>'type' in ('ticket', 'resolution', 'massnahme')) "
        "from incidents i where i.status = 'resolved' and i.resolved_at >= :seit and exists (select 1 from "
        "jsonb_array_elements(i.causal_context::jsonb) e where e->>'type' = 'error_cluster' and e->>'regel' is not null)"),
        {"seit": seit})).all()
    return [{"id": i, "host": h or "", "titel": (t or "")[:160], "geschlossen": z, "beginn": b, "nachrichten": n or 0,
             "massnahmen": list(m or [])} for i, h, t, z, b, n, m in rows]


async def stellen(jetzt: datetime | None = None) -> dict:
    """Stündlich: Kandidaten wählen, Maßnahmen am Host im Zeitraum prüfen (Graylog), Fragen anlegen und mailen."""
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.services.error_clusters import notify
    from app.services.log_watch import loesungen
    from app.services.log_watch.hosts import norm
    from app.services.log_watch.lauf import _graylog_connector
    jetzt = jetzt or datetime.now(timezone.utc)
    o = await _os()
    r = await o.search(index=INDEX, body={"size": 1000, "_source": ["incident_id", "erstellt"],
                                          "query": {"range": {"erstellt": {"gte": (jetzt - timedelta(days=30)).isoformat()}}}})
    gefragt = {h["_source"]["incident_id"] for h in r["hits"]["hits"]}
    heute = sum(1 for h in r["hits"]["hits"] if str(h["_source"].get("erstellt", ""))[:10] == jetzt.date().isoformat())
    async with AsyncSessionLocal() as db:
        incs = await _incident_daten(db, jetzt - timedelta(hours=24))
        app_url = await notify._public_url(db)
    # beobachtete Maßnahmen am Host im Zeitraum (sudo/Ansible, loesungen.klassifiziere) — nur für die Vorauswahl
    gl = await _graylog_connector()
    # Mail-Empfänger je offenem Incident vorab (CheckMK-Filter der Abonnenten) — sie bestimmen die Reihenfolge
    empf: dict[str, tuple[list[str], str]] = {}
    async with AsyncSessionLocal() as db:
        for i in offen(incs, gefragt, jetzt):
            empf[i["id"]] = await notify.empfaenger(db, [i["host"]])
    vor = kandidaten(incs, gefragt, heute, jetzt, {k for k, (to, _) in empf.items() if to})
    gestellt, uebersprungen = [], []
    for i in vor:
        if gl is not None and i.get("beginn"):
            try:
                akt, _ = await loesungen.aktionen(gl, i["beginn"], i["geschlossen"])
                am_host = [a for a in akt if a[1] == norm(i["host"])]
                if am_host:
                    uebersprungen.append({"incident": i["id"], "grund": f"Maßnahme beobachtet: {am_host[0][2]}"})
                    continue
            except Exception as e:  # noqa: BLE001 — ohne Graylog wird trotzdem gefragt
                log.warning("KI-Frage: Graylog-Prüfung %s fehlgeschlagen: %s", i["id"], e)
        fid = uuid.uuid4().hex[:16]
        frage = {"id": fid, "incident_id": i["id"], "host": i["host"], "titel": i["titel"], "frage": frage_text(i),
                 "optionen": {w: v[0] for w, v in WAHL.items()}, "erstellt": jetzt.isoformat(), "status": "offen",
                 "kanaele": ["mail", "konsole"]}
        await o.index(index=INDEX, id=fid, body=frage, refresh=True)
        links = {w: f"{app_url}/api/ki-fragen/{fid}/antwort?wahl={w}&t={token(settings.secret_key, fid, w)}"
                 for w in WAHL}
        to, wahl_grund = empf.get(i["id"]) or ([], "keine Empfänger ermittelt")
        async with AsyncSessionLocal() as db:
            mails = await notify._send(db, to, f"Kurze Frage der KI: {i['titel'][:80]}", mail_html(frage, links)) if to else 0
        await o.update(index=INDEX, id=fid, body={"doc": {"mails": mails, "empfaenger_wahl": wahl_grund}}, refresh=True)
        gestellt.append({"frage": fid, "incident": i["id"], "mails": mails})
    mit_mail = sum(1 for to, _ in empf.values() if to)
    log.info("KI-Fragen: %d gestellt, %d übersprungen (Kandidaten %d, offen %d, davon mit Mail-Empfänger %d, heute "
             "schon %d)", len(gestellt), len(uebersprungen), len(vor), len(empf), mit_mail, heute)
    return {"gestellt": gestellt, "uebersprungen": uebersprungen, "heute_vorher": heute,
            "offen": len(empf), "offen_mit_empfaenger": mit_mail}


async def beantworte(frage_id: str, wahl: str, von: str, text_: str = "") -> dict:
    """Antwort eintragen (Mail-Link oder Konsole) und wirken lassen: Maßnahme am Incident, Urteil an den Nachrichten."""
    from app.core.database import AsyncSessionLocal
    from app.models.workflow import Incident
    w = wirkung(wahl)
    o = await _os()
    try:
        f = (await o.get(index=INDEX, id=frage_id))["_source"]
    except Exception:  # noqa: BLE001
        return {"fehler": f"Frage {frage_id} nicht gefunden"}
    if f.get("status") != "offen":
        return {"fehler": f"Frage {frage_id} ist schon beantwortet ({f.get('antwort')}, von {f.get('beantwortet_von')})"}
    jetzt = datetime.now(timezone.utc)
    erg: dict = {"frage": frage_id, "wahl": wahl, "wirkung": w}
    if w["massnahme"]:
        async with AsyncSessionLocal() as db:
            inc = await db.get(Incident, uuid.UUID(f["incident_id"]))
            if inc is not None:
                inc.causal_context = list(inc.causal_context or []) + [{
                    "type": "massnahme", "art": w["massnahme"], "von": von, "zeit": jetzt.isoformat(),
                    "quelle": f"KI-Frage {frage_id}", "text": text_[:500]}]
                await db.commit()
                erg["massnahme_gebucht"] = True
    if w["urteil"]:
        from app.services.log_watch import nachricht
        try:
            n = await nachricht.urteile(w["urteil"], w["relevanz"], w["kriterien"], "mensch",
                                        f"Antwort auf KI-Frage {frage_id} ({WAHL[wahl][0]}) von {von}",
                                        filtertext=f"incident:{f['incident_id']}")
            erg["nachrichten_beurteilt"] = n
        except Exception as e:  # noqa: BLE001 — die Antwort bleibt, der Ausfall ist benannt
            erg["urteil_fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
    await o.update(index=INDEX, id=frage_id, refresh=True, body={"doc": {
        "status": "beantwortet", "antwort": wahl, "antwort_text": text_[:500], "beantwortet_von": von,
        "beantwortet": jetzt.isoformat(), "wirkung": {k: v for k, v in erg.items() if k != "frage"}}})
    log.info("KI-Frage %s beantwortet: %s von %s", frage_id, wahl, von)
    return erg
