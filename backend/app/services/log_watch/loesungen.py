"""Lösungswege: lernen, WIE ein Fehler behoben wurde — gemessen, nicht geglaubt.

Nutzer 12.11.2026 (über die vi-mcp-Sitzung): „In Centralstation soll auch trainiert werden wie man Fehler behoben hat.
Bspw. Bei einem incident“ — „Testsystem, direkt bauen“. Verfahren nach vi-mcp skills.py/bandit.py: Lösungsweg im Modus
test, gezählt wird Erfolg ÜBER der Basisrate, wer wiederholt scheitert, wird strittig (sichtbar, nicht gelöscht);
ausgeführt wird nie etwas — ein Lösungsweg ist Text mit Beleg.

Episode   = ein Incident der Incident-Regeln (Kontext `error_cluster` mit `regel` und `schluessel`), Host, Beginn, Ende.
Maßnahme  = was in der Episode am Host TATSÄCHLICH geändert wurde, aus festem Vokabular (MASSNAHMEN):
            - beobachtet in Graylog: sudo-Befehle (systemctl/service/docker/reboot/apt) und Ansible-Module mit
              state=restarted|reloaded|started|stopped bzw. reboot/apt — lesende Befehle (docker logs, systemctl status)
              zählen nie;
            - dokumentiert in CentralStation: Konsolen-Sitzung zum Incident, Rezept aus dem Incident, Lösungs-Eintrag.
Behoben   = Episode zu UND derselbe Schlüssel am selben Host eröffnet binnen NACHLAUF (24 h) nach dem Ende keine neue
            Episode. Jüngere Episoden (Ende < NACHLAUF her) sind `offen` — benannt, nicht mitgezählt.
Basisrate = Anteil behobener Episoden desselben Problems OHNE jede Maßnahme („verschwindet von selbst“). Eine Maßnahme
            zählt erst, wenn ihre Quote darüber liegt — „kam zuletzt vor OK“ allein wäre post hoc.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

INDEX = "cs-log-loesungen"
NACHLAUF = timedelta(hours=24)
#: vor dem Beginn zählt eine Maßnahme nicht (sie kann den Fehler ausgelöst haben); danach bis zum Ende
VORLAUF = timedelta(minutes=0)
MIN_N = 5                 # Episoden mit der Maßnahme, bevor eine Quote genannt wird
MIN_N_BESTAETIGT = 10
STRITTIG_N = 3            # so oft gezeigt/angewandt, nie geholfen → strittig
MODI = ("zu_wenig", "test", "bestaetigt", "strittig")
HOLDOUT_ANTEIL = 0.3

#: Graylog-Abfrage nur auf ändernde Befehle (gemessen 12.11.: ~7 000 Treffer in 14 Tagen; das Überwachungsskript
#: `docker logs` allein wären 30 000 je Woche)
GRAYLOG_QUERY = (
    '(message:"COMMAND=" AND (message:"systemctl restart" OR message:"systemctl start" OR message:"systemctl stop" '
    'OR message:"systemctl reload" OR message:"docker restart" OR message:"docker start" OR message:"docker stop" '
    'OR message:"docker compose" OR message:"docker-compose" OR message:service OR message:reboot '
    'OR message:"apt-get install" OR message:"apt install" OR message:"apt upgrade" OR message:"apt-get upgrade") '
    'AND NOT message:"BECOME-SUCCESS") OR (message:"Invoked with" AND (message:"state=restarted" '
    'OR message:"state=reloaded" OR message:"state=started" OR message:"state=stopped" OR message:"ansible-reboot"))')

_VERB = {"restart": "neustart", "restarted": "neustart", "start": "start", "started": "start", "stop": "stopp",
         "stopped": "stopp", "reload": "reload", "reloaded": "reload"}
_UNIT = r"([\w@.:-]+?)(?:\.service)?"
#: (Regex, Bildung des Vokabular-Namens) — ein Name je Maßnahme, egal ob per sudo oder Ansible (ein Ding, ein Name)
MUSTER: tuple[tuple[re.Pattern, object], ...] = (
    (re.compile(r"COMMAND=\S*systemctl\s+(?:--\S+\s+)*(restart|start|stop|reload)\s+" + _UNIT + r"(?:\s|\"|$)"),
     lambda m: f"dienst-{_VERB[m[1]]}:{m[2]}"),
    (re.compile(r"COMMAND=\S*service\s+" + _UNIT + r"\s+(restart|start|stop|reload)\b"),
     lambda m: f"dienst-{_VERB[m[2]]}:{m[1]}"),
    (re.compile(r"COMMAND=\S*docker(?:-compose|\s+compose)\b[^\"]*?\b(restart|up|down|stop|start)\b"),
     lambda m: f"docker-compose:{m[1]}"),
    (re.compile(r"COMMAND=\S*docker\s+(restart|start|stop)\s+([\w.-]+)"),
     lambda m: f"docker-{_VERB[m[1]]}:{m[2]}"),
    (re.compile(r"COMMAND=\S*(?:reboot|shutdown\s+-r)\b"), lambda m: "host-neustart"),
    (re.compile(r"COMMAND=\S*apt(?:-get)?\s+(?:-\S+\s+)*(install|upgrade|dist-upgrade)\b"),
     lambda m: f"paket-{m[1]}"),
    (re.compile(r"ansible-(?:ansible\.(?:builtin|legacy)\.)?(?:systemd|systemd_service|service)\s+Invoked with\s+"
                r"(?=.*\bname=([\w@.:-]+))(?=.*\bstate=(restarted|reloaded|started|stopped))"),
     lambda m: f"dienst-{_VERB[m[2]]}:{re.sub(r'.service$', '', m[1])}"),
    (re.compile(r"ansible-(?:ansible\.(?:builtin|legacy)\.)?reboot\s+Invoked with"), lambda m: "host-neustart"),
)
#: Präfixe des Vokabulars — erschöpfend (Test prüft, dass jede Regel einen davon bildet)
ARTEN = ("dienst-neustart", "dienst-start", "dienst-stopp", "dienst-reload", "docker-neustart", "docker-start",
         "docker-stopp", "docker-compose", "host-neustart", "paket-install", "paket-upgrade", "paket-dist-upgrade",
         "konsole", "rezept", "loesungseintrag")
_HOST_IM_TEXT = re.compile(r"(?:^|\s)([A-Za-z][\w-]*(?:\.[\w-]+)*)\s+(?:sudo|python3)\b")


def klassifiziere(text: str) -> str | None:
    """Maßnahme aus einer Logzeile (Vokabular) oder None — lesende Befehle und Unbekanntes sind keine. Rein."""
    for rx, bilde in MUSTER:
        m = rx.search(text or "")
        if m:
            return bilde(m)
    return None


def host_der_zeile(source: str, text: str) -> str:
    """Bei Syslog-Weiterleitung steht der Host im Text („… mu-img-cueapp03 sudo …“), source ist dann eine IP. Rein."""
    from app.services.log_watch.hosts import norm
    m = _HOST_IM_TEXT.search(text or "")
    return norm(m[1] if m else source)


# ── Episoden ──────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Episode:
    id: str
    host: str                       # norm(primary_host)
    schluessel: str
    regel: str
    beginn: datetime
    ende: datetime | None
    titel: str = ""
    massnahmen: dict[str, list[str]] = field(default_factory=dict)   # Maßnahme → Belege (Logzeilen, gekürzt)
    behoben: bool | None = None     # None = offen (Nachlauf noch nicht vorbei) bzw. nicht zu
    aufgeraeumt: bool = False       # per Aufräumaktion geschlossen — kein Ende des Fehlers, nie gezählt


def problem(e: Episode) -> str:
    """Das Problem, für das ein Lösungsweg gelten soll. Ausbruch/Kritisch/Flotte: der Schlüssel (Muster, Signatur,
    Dienst — gilt über Hosts). Host-Incident (I-HOST): der Schlüssel ist der Host selbst. Rein."""
    return e.schluessel or f"{e.regel}:?"


def setze_behoben(eps: list[Episode], jetzt: datetime) -> None:
    """behoben = zu und binnen NACHLAUF keine neue Episode mit gleichem Schlüssel am gleichen Host. Rein (in place)."""
    je: dict[tuple, list[datetime]] = {}
    for e in eps:
        je.setdefault((e.schluessel, e.host), []).append(e.beginn)
    for e in eps:
        if e.ende is None or jetzt - e.ende < NACHLAUF:
            e.behoben = None
            continue
        e.behoben = not any(e.ende < b <= e.ende + NACHLAUF for b in je[(e.schluessel, e.host)])


def ordne_zu(eps: list[Episode], aktionen: list[tuple[datetime, str, str, str]]) -> None:
    """Maßnahmen (Zeit, Host, Maßnahme, Zeile) den Episoden am selben Host zwischen Beginn und Ende zu. Rein."""
    je_host: dict[str, list[tuple]] = {}
    for a in aktionen:
        je_host.setdefault(a[1], []).append(a)
    for e in eps:
        ende = e.ende or datetime.max.replace(tzinfo=timezone.utc)
        for zeit, _, massnahme, zeile in je_host.get(e.host, []):
            if e.beginn - VORLAUF <= zeit <= ende:
                e.massnahmen.setdefault(massnahme, []).append(zeile[:300])


# ── Lernen ────────────────────────────────────────────────────────────────────────────────────────────────
def wilson_unten(k: int, n: int, z: float = 1.645) -> float:
    """Untere Grenze (90 %, einseitig 95 %) der Erfolgsquote — vorsichtig bei kleinen n. Rein."""
    if n == 0:
        return 0.0
    p = k / n
    nenner = 1 + z * z / n
    mitte = p + z * z / (2 * n)
    rand = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (mitte - rand) / nenner)


def bewerte(eps: list[Episode]) -> list[dict]:
    """Je (Problem, Maßnahme) und je (Regel, Maßnahme): n, behoben, Quote, Basisrate ohne Maßnahme, Vorsprung, Modus
    mit Grund, Belege (Incident-IDs). Beta(1+k, 1+n−k)-Mittel wie vi-mcp bandit.py. Rein."""
    fertig = [e for e in eps if e.behoben is not None]
    zeilen: list[dict] = []
    for ebene, schl in (("problem", problem), ("regel", lambda e: e.regel)):
        gruppen: dict[str, list[Episode]] = {}
        for e in fertig:
            gruppen.setdefault(schl(e), []).append(e)
        for g, liste in gruppen.items():
            ohne = [e for e in liste if not e.massnahmen]
            basis_k, basis_n = sum(1 for e in ohne if e.behoben), len(ohne)
            basis = (1 + basis_k) / (2 + basis_n)
            alle_m = Counter(m for e in liste for m in e.massnahmen)
            for m in alle_m:
                mit = [e for e in liste if m in e.massnahmen]
                k, n = sum(1 for e in mit if e.behoben), len(mit)
                quote = (1 + k) / (2 + n)
                unten = wilson_unten(k, n)
                if n >= STRITTIG_N and k == 0:
                    mod, grund = "strittig", f"{n}× angewandt, nie behoben (gezählt, nicht gelöscht)"
                elif n < MIN_N:
                    mod, grund = "zu_wenig", f"{n} Episoden mit der Maßnahme — Quote erst ab {MIN_N}"
                elif n >= MIN_N_BESTAETIGT and basis_n >= MIN_N and unten > basis:
                    mod, grund = "bestaetigt", (f"untere Grenze {unten:.0%} über der Basisrate {basis:.0%} "
                                                f"({basis_n} Episoden ohne Maßnahme)")
                else:
                    mod, grund = "test", (f"Quote {quote:.0%} (untere Grenze {unten:.0%}) gegen Basisrate {basis:.0%} "
                                          f"aus {basis_n} Episoden ohne Maßnahme"
                                          + ("" if basis_n >= MIN_N else f" — Basisrate erst ab {MIN_N} belastbar"))
                zeilen.append({"ebene": ebene, "problem": g, "massnahme": m, "n": n, "behoben": k,
                               "quote": round(quote, 3), "quote_unten": round(unten, 3),
                               "basisrate": round(basis, 3), "basis_n": basis_n, "vorsprung": round(quote - basis, 3),
                               "modus": mod, "grund": grund,
                               "belege": [e.id for e in sorted(mit, key=lambda e: e.beginn, reverse=True)[:10]],
                               "beispiel": next((z for e in mit for z in e.massnahmen[m]), "")[:300]})
    reihen = {"bestaetigt": 0, "test": 1, "zu_wenig": 2, "strittig": 3}
    return sorted(zeilen, key=lambda z: (reihen[z["modus"]], -z["vorsprung"], -z["n"]))


def pruefe_zeitlich(eps: list[Episode]) -> dict:
    """Halten die Ranglisten? Gelernt auf den älteren 70 %, geprüft auf den jüngeren 30 % — gleiche (Problem,
    Maßnahme) mit Vorsprung > 0 im Training: wie oft auch im Holdout über der dortigen Basisrate. Rein."""
    fertig = sorted((e for e in eps if e.behoben is not None), key=lambda e: e.beginn)
    k = int(len(fertig) * (1 - HOLDOUT_ANTEIL))
    alt = {(z["problem"], z["massnahme"]): z for z in bewerte(fertig[:k]) if z["ebene"] == "problem"}
    neu = {(z["problem"], z["massnahme"]): z for z in bewerte(fertig[k:]) if z["ebene"] == "problem"}
    paare = [(a, neu[s]) for s, a in alt.items() if a["vorsprung"] > 0 and s in neu and neu[s]["n"] >= 3]
    return {"train_episoden": k, "holdout_episoden": len(fertig) - k, "gepruefte_paare": len(paare),
            "gehalten": sum(1 for _, b in paare if b["vorsprung"] > 0),
            "beispiele": [{"problem": a["problem"], "massnahme": a["massnahme"], "vorsprung_train": a["vorsprung"],
                           "vorsprung_holdout": b["vorsprung"], "n_holdout": b["n"]} for a, b in paare[:10]]}


def zusammenfassung(eps: list[Episode], zeilen: list[dict], jetzt: datetime) -> dict:
    fertig = [e for e in eps if e.behoben is not None]
    mit = [e for e in fertig if e.massnahmen]
    ohne = [e for e in fertig if not e.massnahmen]
    return {"zeit": jetzt.isoformat(), "episoden": len(eps), "offen": len(eps) - len(fertig),
            "davon_aufgeraeumt": sum(1 for e in eps if e.aufgeraeumt),
            "mit_massnahme": len(mit), "ohne_massnahme": len(ohne),
            "behoben_mit": sum(1 for e in mit if e.behoben), "behoben_ohne": sum(1 for e in ohne if e.behoben),
            "je_regel": dict(Counter(e.regel for e in fertig)),
            "massnahmen": dict(Counter(m for e in eps for m in e.massnahmen).most_common(30)),
            "modi": dict(Counter(z["modus"] for z in zeilen)),
            "wege": zeilen[:200], "holdout": pruefe_zeitlich(eps)}


# ── Daten ─────────────────────────────────────────────────────────────────────────────────────────────────
async def episoden(db, tage: float) -> list[Episode]:
    """Incidents der Incident-Regeln der letzten `tage` (mit Kontext regel/schluessel) als Episoden, samt den in
    CentralStation dokumentierten Maßnahmen (Lösungs-Eintrag, Rezept)."""
    from sqlalchemy import text

    from app.services.log_watch.hosts import norm
    seit = datetime.now(timezone.utc) - timedelta(days=tage)
    rows = (await db.execute(text(
        "select i.id::text, i.primary_host, i.title, i.created_at, i.resolved_at, i.status, e->>'regel', "
        "e->>'schluessel', (select jsonb_agg(x) from jsonb_array_elements(i.causal_context::jsonb) x "
        " where x->>'type' in ('resolution','ticket','check_run')) "
        "from incidents i, jsonb_array_elements(i.causal_context::jsonb) e "
        "where e->>'type' = 'error_cluster' and e->>'regel' is not null and i.created_at >= :seit"),
        {"seit": seit})).all()
    eps = []
    gesehen: set[str] = set()
    for iid, host, titel, beginn, ende, status, regel, schl, doku in rows:
        if iid in gesehen:                  # mehrere error_cluster-Einträge (weitere Schlüssel): der erste zählt
            continue
        gesehen.add(iid)
        e = Episode(iid, norm(host or ""), schl or "", regel, beginn, ende if status == "resolved" else None,
                    (titel or "")[:160])
        for x in doku or []:
            if x.get("type") == "resolution":
                e.massnahmen.setdefault("loesungseintrag", []).append(str(x.get("text") or x.get("grund") or "")[:300])
        eps.append(e)
    # Incidents vor den Incident-Regeln (ohne error_cluster-Kontext): Host-Episoden alter Art — Schlüssel host:<Host>,
    # Regel „alt:<kind>“, damit sie nie mit den Regel-Episoden in eine Basisrate fallen
    alt_rows = (await db.execute(text(
        "select i.id::text, i.primary_host, i.title, i.created_at, i.resolved_at, i.status, i.kind, "
        "exists (select 1 from jsonb_array_elements(case when jsonb_typeof(i.causal_context::jsonb) = 'array' "
        "then i.causal_context::jsonb else '[]'::jsonb end) g where g->>'type' = 'geschlossen') "
        "from incidents i where i.created_at >= :seit and not exists (select 1 from "
        "jsonb_array_elements(case when jsonb_typeof(i.causal_context::jsonb) = 'array' then i.causal_context::jsonb "
        "else '[]'::jsonb end) e where e->>'type' = 'error_cluster' and e->>'regel' is not null)"), {"seit": seit})).all()
    for iid, host, titel, beginn, ende, status, kind, aufgeraeumt in alt_rows:
        if iid not in gesehen:
            gesehen.add(iid)
            # per Aufräumen geschlossen (Nutzer 07.11.: Altlasten) ist kein Ende des Fehlers — Episode bleibt offen
            eps.append(Episode(iid, norm(host or ""), f"host:{host}", f"alt:{kind}", beginn,
                               ende if status == "resolved" and not aufgeraeumt else None, (titel or "")[:160],
                               aufgeraeumt=bool(aufgeraeumt)))
    rez = (await db.execute(text("select source_incident_id::text, title from recipes where source_incident_id is not null"))).all()
    nach_id = {e.id: e for e in eps}
    for iid, titel in rez:
        if iid in nach_id:
            nach_id[iid].massnahmen.setdefault("rezept", []).append((titel or "")[:300])
    return eps


async def konsole_zuordnen(eps: list[Episode]) -> int:
    """Konsolen-Sitzungen mit Bezug incident:<id> (cs-gespraech-*, quelle:konsole) als Maßnahme „konsole“."""
    from app.core.opensearch import get_opensearch
    ids = [f"incident:{e.id}" for e in eps]
    if not ids:
        return 0
    r = await get_opensearch().search(index="cs-gespraech-*", ignore_unavailable=True, body={
        "size": 0, "query": {"bool": {"filter": [{"term": {"hashtags": "quelle:konsole"}},
                                                 {"terms": {"hashtags": ids[:65000]}}]}},
        "aggs": {"i": {"terms": {"field": "hashtags", "include": "incident:.*", "size": 10000}}}})
    gefunden = {b["key"].split(":", 1)[1] for b in r.get("aggregations", {}).get("i", {}).get("buckets", [])}
    for e in eps:
        if e.id in gefunden:
            e.massnahmen.setdefault("konsole", []).append(f"Konsolen-Sitzung zu incident:{e.id}")
    return len(gefunden)


async def aktionen(gl, seit: datetime, bis: datetime) -> tuple[list[tuple[datetime, str, str, str]], dict]:
    """Ändernde Befehle aus Graylog, tageweise (je Abruf ≤ 10 000). Rückgabe: (Aktionen, Zählung)."""
    out: list[tuple[datetime, str, str, str]] = []
    z = Counter()
    t = seit
    while t < bis:
        t2 = min(bis, t + timedelta(days=1))
        msgs, gesamt = await gl.search_window(GRAYLOG_QUERY, t.isoformat().replace("+00:00", "Z"),
                                              t2.isoformat().replace("+00:00", "Z"),
                                              fields=["source", "timestamp", "message"])
        z["zeilen"] += len(msgs)
        if gesamt > len(msgs):
            z["gekappt"] += gesamt - len(msgs)
        for m in msgs:
            text_ = str(m.get("message") or "")
            mass = klassifiziere(text_)
            if not mass:
                z["nicht_klassifiziert"] += 1
                continue
            try:
                zeit = datetime.fromisoformat(str(m.get("timestamp")).replace("Z", "+00:00"))
            except ValueError:
                z["ohne_zeit"] += 1
                continue
            out.append((zeit, host_der_zeile(str(m.get("source") or ""), text_), mass, text_))
        t = t2
    # dieselbe Zeile kommt über Syslog und Filebeat doppelt an — je (Zeit auf die Sekunde, Host, Maßnahme) einmal
    eindeutig = {(a[0].replace(microsecond=0), a[1], a[2]): a for a in out}
    z["doppelt"] = len(out) - len(eindeutig)
    z["aktionen"] = len(eindeutig)
    return list(eindeutig.values()), dict(z)


async def lerne(tage: float = 30) -> dict:
    """Nachtlauf: Episoden, Maßnahmen, Bewertung, speichern; bestätigte Wege in die VI (vi_lerne, Schlüssel je Weg)."""
    from app.core.database import AsyncSessionLocal
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.lauf import _graylog_connector
    jetzt = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        eps = await episoden(db, tage)
    zaehl: dict = {}
    gl = await _graylog_connector()
    if gl is not None and eps:
        akt, zaehl = await aktionen(gl, min(e.beginn for e in eps), jetzt)
        ordne_zu(eps, akt)
    else:
        zaehl = {"fehler": "kein Graylog-Konnektor" if gl is None else "keine Episoden"}
    try:
        zaehl["konsole_incidents"] = await konsole_zuordnen(eps)
    except Exception as ex:  # noqa: BLE001
        zaehl["konsole_fehler"] = f"{type(ex).__name__}: {str(ex)[:200]}"
    setze_behoben(eps, jetzt)
    z = zusammenfassung(eps, bewerte(eps), jetzt)
    z["quellen"] = {"incidents": f"incidents (Kontext error_cluster, letzte {tage} Tage)", "graylog": zaehl}
    z["vi"] = await _in_die_vi([w for w in z["wege"] if w["modus"] == "bestaetigt"])
    os_ = get_opensearch()
    if not await os_.indices.exists(index=INDEX):
        await os_.indices.create(index=INDEX, body={"mappings": {"dynamic": False, "properties": {
            "zeit": {"type": "date"}}}})
    await os_.index(index=INDEX, id=z["zeit"], body=z, refresh=True)
    log.info("Lösungswege: %s Episoden, %s mit Maßnahme, Modi %s", z["episoden"], z["mit_massnahme"], z["modi"])
    return z


def vi_weg(w: dict) -> dict:
    """Argumente für vi_solved aus einem bestätigten Lösungsweg. Der Name loesung-<problem>-<maßnahme> (nur a–z, 0–9, „-“
    wie VI-Skill-Namen) ist die Identität: gleicher Name entwickelt den Weg weiter statt ihn zu verdoppeln. Trigger: host:<h> bei Host-Problemen, sonst
    fehler:<schlüssel>. Incident-IDs stehen in Anleitung und Grund — evidence nimmt nur VI-Objekt-IDs. Rein."""
    prob = w["problem"]
    trig = [prob] if prob.startswith("host:") else [f"fehler:{prob}"]
    anleitung = (f"Problem: {prob}\nMaßnahme: {w['massnahme']}\nBeobachtete Befehlszeile:\n{w['beispiel']}\n\n"
                 f"Gemessen von CentralStation (loesungen.py): behoben in {w['behoben']} von {w['n']} Episoden "
                 f"(Quote {w['quote']:.0%}, untere Grenze {w['quote_unten']:.0%}) gegen {w['basisrate']:.0%} ohne "
                 f"Maßnahme ({w['basis_n']} Episoden). Behoben = derselbe Fehler kehrt binnen 24 h am Host nicht "
                 f"wieder. Belege (CentralStation-Incidents): {', '.join(w['belege'])}")
    def slug(x: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", x.lower()).strip("-")
    return {"name": f"loesung-{slug(prob)}-{slug(w['massnahme'])}"[:200], "triggers": trig, "steps": [w["massnahme"]],
            "instructions": anleitung,
            "reason": f"CentralStation: {w['grund']} — Incidents {', '.join(w['belege'][:5])}"}


async def _in_die_vi(wege: list[dict]) -> dict:
    """Bestätigte Lösungswege als Fähigkeit in die VI (vi_solved) — dann erscheinen sie vor passenden Aktionen in allen
    Sitzungen, und ihr Nutzen wird dort gemessen. Kennt der Server vi_solved nicht, als Erkenntnis (vi_lerne, key)."""
    if not wege:
        return {"geschrieben": 0, "grund": "kein bestätigter Lösungsweg"}
    from app.core.database import AsyncSessionLocal
    from app.services.connectors.vi_mcp import ViMcpFehler, load_vi_mcp_connector
    async with AsyncSessionLocal() as db:
        k = await load_vi_mcp_connector(db)
    if k is None:
        return {"geschrieben": 0, "grund": "kein vi-mcp-Konnektor"}
    n, weg_art, fehler = 0, "vi_solved", []
    for w in wege[:20]:
        arg = vi_weg(w)
        try:
            try:
                a = await k.geloest(**arg)
            except ViMcpFehler as ex:
                if "vi_solved" not in str(ex) and "unknown" not in str(ex).lower():
                    raise
                weg_art = "vi_lerne"
                a = await k.lerne(arg["instructions"][:1000], ["skill:loesungsweg", *arg["triggers"]],
                                  f"CentralStation loesungen.py, Incidents {', '.join(w['belege'][:5])}", 2,
                                  key=arg["name"], ausgang="gut")
            n += 0 if getattr(a, "fehler", False) else 1
        except Exception as ex:  # noqa: BLE001
            fehler.append(f"{type(ex).__name__}: {str(ex)[:120]}")
    return {"geschrieben": n, "weg": weg_art, "fehler": fehler[:3]}


async def lade() -> dict | None:
    from app.core.opensearch import get_opensearch
    try:
        r = await get_opensearch().search(index=INDEX, ignore_unavailable=True,
                                          body={"size": 1, "sort": [{"zeit": "desc"}]})
    except Exception as e:  # noqa: BLE001
        log.warning("Lösungswege nicht geladen: %s", e)
        return None
    h = r.get("hits", {}).get("hits", [])
    return h[0]["_source"] if h else None


def fuer(stand: dict | None, schluessel: str = "", regel: str = "", host: str = "") -> dict:
    """Lösungswege für ein Problem — so wurde es zuletzt behoben, mit Quote, Basisrate und Belegen. Rein."""
    if not stand:
        return {"wege": [], "grund": "noch nicht gelernt (stündlich zur Minute 50 oder POST /log-watch/loesungen/lernen)"}
    from app.services.log_watch.hosts import norm
    ziele = {schluessel} if schluessel else set()
    if host and not schluessel:
        ziele.add(f"host:{host}")
        ziele.add(f"host:{norm(host)}")
    wege = [w for w in stand.get("wege") or [] if (w["ebene"] == "problem" and w["problem"] in ziele)
            or (regel and w["ebene"] == "regel" and w["problem"] == regel)]
    return {"stand": stand.get("zeit"), "wege": wege[:20],
            "hinweis": "Lösungswege sind gemessene Text-Hinweise, keine Anweisung — ausgeführt wird nichts",
            "grund": "" if wege else "kein Lösungsweg für dieses Problem gemessen"}
