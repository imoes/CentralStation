"""Feldkatalog: jedes Feld jeder Quelle (Graylog, Wazuh, CheckMK) mit Typ, Kardinalität,
Abdeckung und Rolle.

Gemessen wird in der Quelle selbst (quellen/*), es werden keine Nachrichten kopiert. Die
Rolle bestimmt, wie ein Feld später ausgewertet wird — über alle Quellen gleich:

  entitaet   Host, Container, Agent, Service → Hashtag #host:…, Netzplan-Knoten, Ausbreitung
  kategorie  Level, Zustand, HTTP-Code       → Verteilung, Anstieg, neuer Wert
  kennung    Regel-ID, Check, Fehlercode     → neu, Anstieg
  messwert   Dauer, Bytes                    → Ausreißer
  freitext   message, full_log, plugin_output → Text, Muster (Drain)
  ignorieren IDs, Zeitstempel, Offsets       → nie aggregieren
  unbekannt  im Messfenster keine Werte      → benannt, nicht geraten

Regeln zuerst nach Namen (gilt auch ohne Messung), dann nach Messung. Eine vom Admin
gesetzte Rolle überschreibt keine Messung. Ein Feld, das die Quelle nicht mehr kennt,
wird „verschwunden“ — es bleibt im Katalog stehen.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)

BEISPIELE_JE_FELD = 5
#: Bis zu dieser Kardinalität gilt ein Feld als Kategorie (endliche Werteliste).
KATEGORIE_MAX = 50
#: Ab diesem Anteil verschiedener Werte je Nachricht ist ein Feld eine Einmal-Kennung.
EINMALIG_ANTEIL = 0.5
EINMALIG_MIN = 1000
#: Mehr verschiedene Werte taugen nicht als Hashtag (gemessen 06.11.: Ports 62 000,
#: Domains 9 000) — das Feld wird ignoriert, statt den Tag-Raum zu fluten.
KENNUNG_MAX = 5000

ROLLEN = ("entitaet", "kategorie", "kennung", "messwert", "freitext", "ignorieren", "unbekannt")
#: Rollen, deren Werte Hashtags und Kategorien liefern — für sie werden Beispielwerte geholt.
WERT_ROLLEN = ("entitaet", "kategorie", "kennung")

# Namen über alle Quellen. Wazuh schreibt verschachtelt (rule.level), CheckMK-Labels als
# label:<schlüssel>; Trenner sind daher "_" und ".".
_KENNUNG = {"rule.id", "rule.description", "rule.groups", "rule.mitre.id",
            "rule.mitre.technique", "rule.mitre.tactic", "decoder.name", "decoder.parent",
            "check_command"}
_IGNORIEREN = {"_id", "id", "timestamp", "@timestamp", "@version", "streams", "created",
               "log_timestamp", "log_offset", "log_file_inode", "log_file_device_id",
               "log_flags", "agent.id"}
_IGNORIEREN_RE = re.compile(r"^(gl2_|@metadata|_)|[._](id|uuid|offset|inode|hash|created)$"
                            r"|ephemeral_id$|(^|[._])timestamp\d*$")
_FREITEXT = {"message", "full_message", "short_message", "full_log", "previous_output",
             "plugin_output", "long_plugin_output", "data.win.system.message"}
_ENTITAET = {"source", "host", "hostname", "host_name", "beat_hostname", "container_name",
             "application_name", "servicename", "service", "image_name", "agent_name",
             "server", "program", "application", "log_file_path", "agent.name", "agent.ip",
             "manager.name", "predecoder.hostname", "location"}
_ENTITAET_RE = re.compile(
    r"(^|[._])(hostname|host_name|computer|container_name|service_name|app_name|computer_name"
    r"|ip|srcip|dstip)$|_ip$")
_KATEGORIE = {"level", "facility", "facility_num", "log_level", "http_response_code",
              "http_status", "status", "severity", "http_method", "input_type", "source_type",
              "environment", "beats_type", "event_action", "event_outcome", "log_level_source",
              "rule.level", "state", "state_type", "host_state", "acknowledged", "in_downtime"}
_KATEGORIE_RE = re.compile(r"[._](code|status|level|type|method|severity|outcome|state)$")
_MESSWERT_RE = re.compile(r"(^|[._])(port|srcport|dstport|bytes|duration|size|length|latency"
                          r"|count|time_num)$|_num$")


def rolle_nach_name(feld: str, numerisch: bool) -> tuple[str, str] | None:
    """Rolle allein aus dem Namen — None, wenn erst die Messung entscheidet."""
    if feld in _KENNUNG:
        return "kennung", "Regel/Check (Name)"
    if feld in _IGNORIEREN or _IGNORIEREN_RE.search(feld):
        return "ignorieren", "Kennung/Zeitstempel je Nachricht (Name)"
    if feld in _FREITEXT:
        return "freitext", "Nachrichtentext (Name)"
    if feld in _ENTITAET or _ENTITAET_RE.search(feld):
        return "entitaet", "Host/Container/Agent/Service (Name)"
    if feld in _KATEGORIE or _KATEGORIE_RE.search(feld):
        return "kategorie", "Level/Zustand/Code (Name)"
    if _MESSWERT_RE.search(feld):
        return "messwert", "Port/Bytes/Dauer/Zähler (Name)"
    if numerisch:
        return "messwert", "numerisches Feld"
    return None


def bestimme_rolle(feld: str, numerisch: bool, kardinalitaet: int | None,
                   mit_feld: int | None) -> tuple[str, str]:
    """Rolle und Grund. Reine Funktion — Name zuerst, dann Messung."""
    nach_name = rolle_nach_name(feld, numerisch)
    if nach_name:
        return nach_name
    if not mit_feld or kardinalitaet is None:
        return "unbekannt", "im Messfenster keine Werte"
    if kardinalitaet <= KATEGORIE_MAX:
        return "kategorie", f"{kardinalitaet} verschiedene Werte"
    if kardinalitaet >= EINMALIG_MIN and kardinalitaet / mit_feld >= EINMALIG_ANTEIL:
        return "ignorieren", (f"{kardinalitaet} Werte bei {mit_feld} Nachrichten "
                              f"— fast jeder Wert einmalig")
    if kardinalitaet > KENNUNG_MAX:
        return "ignorieren", f"{kardinalitaet} verschiedene Werte — zu viele für Hashtags"
    return "kennung", f"{kardinalitaet} verschiedene Werte"


def braucht_werte(f: "Feld", stat: dict | None) -> bool:
    """Lohnt es, für dieses Feld Beispielwerte zu holen (Hashtag-/Kategorie-Kandidat)?"""
    return bool(stat and stat.get("mit_feld")) and bestimme_rolle(
        f.name, f.numerisch, stat.get("kardinalitaet"), stat["mit_feld"])[0] in WERT_ROLLEN


@dataclass
class Feld:
    name: str
    typ: str
    numerisch: bool
    #: False für reine Textfelder: die Quelle kann darauf nicht aggregieren (keine
    #: Kardinalität, Abdeckung nur über „Feld vorhanden“).
    aggregierbar: bool = True


def uebernehme(bestand: dict[str, Any], felder: list[Feld], gesamt: int,
               stats: dict[str, dict], jetzt: datetime,
               neue_zeile: Callable[[str], Any]) -> tuple[list[Any], dict]:
    """Katalog einer Quelle fortschreiben. Rein bis auf `neue_zeile(feld)` — testbar ohne DB.

    stats: je Feld {mit_feld, kardinalitaet, beispielwerte?}; ein Feld ohne Eintrag wurde
    nicht gemessen (die Quelle lehnte ab) — das ist nicht dasselbe wie „keine Werte“.
    Rückgabe: (neue Zeilen, Zähler {neu, aktiv, ohne_werte, verschwunden, nicht_gemessen}).
    """
    zaehler = {"neu": 0, "aktiv": 0, "ohne_werte": 0, "verschwunden": 0, "nicht_gemessen": 0}
    neu: list[Any] = []
    gesehen = set()
    for f in felder:
        gesehen.add(f.name)
        s = stats.get(f.name)
        row = bestand.get(f.name)
        if row is None:
            row = neue_zeile(f.name)
            neu.append(row)
            zaehler["neu"] += 1
        row.typ, row.numerisch = f.typ, f.numerisch
        if s is None:
            # Status, Zahlen und Rolle bleiben, wie sie waren.
            zaehler["nicht_gemessen"] += 1
            if row.rolle is None:
                row.status = "nicht_gemessen"
                row.rolle, row.rolle_grund = bestimme_rolle(f.name, f.numerisch, None, None)
            continue
        mit, card = s["mit_feld"], s.get("kardinalitaet")
        row.kardinalitaet, row.mit_feld, row.gemessen_at = card, mit, jetzt
        row.abdeckung = round(mit / gesamt, 6) if gesamt else None
        if s.get("beispielwerte") is not None:
            row.beispielwerte = s["beispielwerte"]
        if mit:
            row.status, row.last_seen = "aktiv", jetzt
        else:
            row.status = "ohne_werte"
        zaehler[row.status] += 1
        if row.rolle_herkunft != "admin":
            row.rolle, row.rolle_grund = bestimme_rolle(f.name, f.numerisch, card, mit)
    for name, row in bestand.items():
        if name not in gesehen and row.status != "verschwunden":
            row.status = "verschwunden"
            zaehler["verschwunden"] += 1
    return neu, zaehler


async def aktualisiere(db, nur: str | None = None) -> dict:
    """Je Quelle: Feldliste holen, messen, Katalog fortschreiben.

    Eine Quelle, die fehlt oder scheitert, steht mit Grund im Ergebnis — die anderen
    laufen trotzdem. Rückgabe: {quelle: Zähler | {"fehler": …}}.
    """
    from sqlalchemy import select

    from app.models.log_watch import FieldCatalog
    from app.services.log_watch.quellen import lade_quellen

    quellen, fehlend = await lade_quellen(db)
    ergebnis: dict[str, dict] = {name: {"fehler": grund} for name, grund in fehlend.items()
                                 if nur in (None, name)}
    for q in quellen:
        if nur not in (None, q.name):
            continue
        try:
            felder = await q.felder()
            gesamt, stats = await q.messe(felder)
        except Exception as e:  # noqa: BLE001 — eine Quelle darf die anderen nicht stoppen
            log.warning("Feldkatalog %s fehlgeschlagen: %s", q.name, e)
            ergebnis[q.name] = {"fehler": f"{type(e).__name__}: {str(e)[:300]}"}
            continue
        bestand = {row.feld: row for row in (await db.execute(
            select(FieldCatalog).where(FieldCatalog.quelle == q.name))).scalars()}
        jetzt = datetime.now(timezone.utc)
        neu, zaehler = uebernehme(
            bestand, felder, gesamt, stats, jetzt,
            lambda feld, quelle=q.name: FieldCatalog(
                quelle=quelle, feld=feld, first_seen=jetzt, rolle_herkunft="regel",
                rolle=None, gemessen_at=None))
        db.add_all(neu)
        await db.commit()
        zaehler.update(felder=len(felder), nachrichten_im_fenster=gesamt, fenster=q.fenster)
        log.info("Feldkatalog %s: %s", q.name, zaehler)
        ergebnis[q.name] = zaehler
    return ergebnis
