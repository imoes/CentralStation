"""Raster: den Stream aller drei Quellen per Skript taggen und bewerten — ohne KI.

Ergebnis je Nachrichtengruppe (Muster × Host, Regel × Agent, Service × Host) ein
`Treffer` mit Relevanz 0–3, Gründen und Hashtags. Hashtags haben genau eine Form,
`art:wert`; sie sind zugleich Filter, Facette und Netzplan-Knoten (Plan „Netzplan =
Suche“). Gespeichert wird später nur Relevanz ≥ 2, je Gruppe und Stunde höchstens
`KONTINGENT` Nachrichten — der Rest wird gezählt, nicht verworfen.

Relevanz:  0 Routine · 1 normal · 2 auffällig (wird Historie) · 3 dringend (geht an die KI)
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from app.services.log_watch.hosts import Hostverzeichnis, norm
from app.services.log_watch.vokabular import dienst_thema, themen

SPEICHERN_AB = 2
KONTINGENT = 5
#: CheckMK-Kritikalität, bei der ein CRIT dringend (3) ist. Werte wie im Host-Tag
#: `criticality`; der Trockenlauf zählt die tatsächlich vorkommenden Werte mit.
KRITISCH = frozenset({"critical", "high", "prod", "business-critical", "hoch", "kritisch"})
#: Mittlere Nachrichtengröße je Quelle in Byte, für die Platzschätzung.
#: Graylog gemessen 06.11. (Stichprobe 500 Fehlerzeilen); Wazuh/CheckMK geschätzt.
BYTES_JE_NACHRICHT = {"graylog": 1468, "wazuh": 2500, "checkmk": 800}
#: Höchstens so viele Hostgruppen-Tags je Nachricht.
MAX_HG = 3


@dataclass
class Treffer:
    quelle: str
    #: stabile Gruppe innerhalb der Quelle: Muster, Regel oder Service
    schluessel: str
    host: str
    relevanz: int
    gruende: list[str]
    hashtags: list[str]
    #: Nachrichten dieser Gruppe im Fenster
    anzahl: int
    beispiel: str = ""
    host_bekannt: bool = True


def _host_tags(name: str, verz: Hostverzeichnis) -> tuple[str, bool, list[str]]:
    e, k, grund = verz.zuordnen(name)
    kanon = e.name if e else k
    tags = [f"host:{kanon}"] if kanon else []
    if e is None:
        tags.append(f"hostliste:{grund}")
        return kanon, False, tags
    if e.ort:
        tags.append(f"ort:{e.ort}")
    tags += [f"hg:{g}" for g in e.hostgruppen[:MAX_HG]]
    return kanon, True, tags


def _ohne_doppel(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in tags if t and not t.endswith(":")))


# ── Graylog ────────────────────────────────────────────────────────────────────

_MELDEND = ("open", "open_digest", "reopen", "renotify")


def bewerte_graylog(templates: dict, events: list, kritisch: dict, stumm: set[str],
                    verz: Hostverzeichnis) -> list[Treffer]:
    """Ein 5-min-Fenster der Fehler-Cluster-Erkennung bewerten.

    templates: Muster-Schlüssel → TemplateTally (mit `hosts`: Zeilen je Quelle)
    events:    Ereignisse der Engine in diesem Fenster (Regeln neu/Anstieg/Ausbreitung)
    kritisch:  critical.scan — (Signatur, Host) → Fund
    stumm:     stumm geschaltete Muster
    """
    ereignis: dict[str, object] = {}
    for ev in events:
        if ev.action not in _MELDEND:
            continue
        alt = ereignis.get(ev.template.key)
        if alt is None or (ev.verdict.notify == "immediate" and alt.verdict.notify != "immediate"):
            ereignis[ev.template.key] = ev
    krit_je_host: dict[str, list[str]] = {}
    for sig, host in kritisch:
        krit_je_host.setdefault(norm(host), []).append(sig)

    out: list[Treffer] = []
    for key, tt in templates.items():
        beispiel = tt.examples[0] if tt.examples else ""
        th = themen(f"{tt.template}\n{beispiel}")
        rel, gruende = 1, ["Fehlerzeile in Graylog"]
        ev = ereignis.get(key)
        if ev is not None:
            rel = 2
            gruende += list(ev.verdict.reasons) or [
                "Muster auffällig: " + ", ".join(sorted(ev.verdict.rules))]
            if ev.verdict.notify == "immediate":
                rel = 3
                gruende.append("Sofort-Meldung der Fehler-Cluster-Erkennung")
        for t in th:
            if t.mindest > rel:
                rel = t.mindest
                gruende.append(f"Thema „{t.text}“ ist nie Routine")
        apps = [e for e, _ in tt.entities.most_common() if e and e not in tt.sources][:3]
        hosts = getattr(tt, "hosts", None) or Counter({s: 0 for s in tt.sources})
        for host, n in hosts.items():
            r, g = rel, list(gruende)
            kanon, bekannt, htags = _host_tags(host, verz)
            sigs = krit_je_host.get(norm(host))
            if sigs:
                r = 3
                g.append("Kritische Kernelmeldung auf diesem Host: " + ", ".join(sorted(set(sigs))))
            if key in stumm:
                r, g = 0, ["Muster stumm geschaltet (Admin)"]
            tags = (["quelle:graylog", f"muster:{key}"] + htags + [f"app:{a}" for a in apps]
                    + [f"thema:{t.id}" for t in th])
            out.append(Treffer("graylog", f"muster:{key}", kanon, r, g, _ohne_doppel(tags),
                               n or tt.count, beispiel[:500], bekannt))
    return out


# ── Wazuh ──────────────────────────────────────────────────────────────────────

def wazuh_relevanz(level: int) -> int:
    """Startwert aus dem Wazuh-Level (0–15)."""
    return 3 if level >= 12 else 2 if level >= 7 else 1 if level >= 4 else 0


def wazuh_zeilen(aggs: dict) -> list[dict]:
    """Aggregation `regel` (terms rule.id) › `agent` (terms agent.name) + `info` (top_hits)."""
    zeilen = []
    for rb in (aggs.get("regel") or {}).get("buckets") or []:
        hits = (((rb.get("info") or {}).get("hits") or {}).get("hits") or [{}])
        rule = ((hits[0] or {}).get("_source") or {}).get("rule") or {}
        for ab in (rb.get("agent") or {}).get("buckets") or []:
            zeilen.append({
                "regel": str(rb["key"]), "level": int(rule.get("level") or 0),
                "beschreibung": rule.get("description") or "",
                "gruppen": list(rule.get("groups") or []),
                "taktiken": list((rule.get("mitre") or {}).get("tactic") or []),
                "agent": str(ab["key"]), "anzahl": int(ab["doc_count"]),
            })
    return zeilen


def bewerte_wazuh(zeilen: list[dict], verz: Hostverzeichnis) -> list[Treffer]:
    out = []
    for z in zeilen:
        rel = wazuh_relevanz(z["level"])
        gruende = [f"Wazuh-Regel {z['regel']}, Level {z['level']}: {z['beschreibung']}"]
        th = themen(z["beschreibung"])
        for t in th:
            if t.mindest > rel:
                rel = t.mindest
                gruende.append(f"Thema „{t.text}“ ist nie Routine")
        kanon, bekannt, htags = _host_tags(z["agent"], verz)
        tags = (["quelle:wazuh", f"regel:{z['regel']}"] + htags
                + [f"regelgruppe:{g}" for g in z["gruppen"][:3]]
                + [f"mitre:{t}" for t in z["taktiken"]] + [f"thema:{t.id}" for t in th])
        out.append(Treffer("wazuh", f"regel:{z['regel']}", kanon, rel, gruende,
                           _ohne_doppel(tags), z["anzahl"], z["beschreibung"][:500], bekannt))
    return out


# ── CheckMK ────────────────────────────────────────────────────────────────────

_ZUSTAND = {"critical": 2, "warning": 1, "unknown": 3}
_ZUSTAND_TAG = {"critical": "crit", "warning": "warn", "unknown": "unknown"}


@dataclass
class CheckmkAbgleich:
    treffer: list[Treffer]
    #: Probleme, die seit dem letzten Abgleich verschwunden sind (Host|Service)
    behoben: list[str]
    #: Zustand jetzt, für den nächsten Abgleich
    zustand: dict[str, str]
    #: Erster Abgleich: der Bestand ist nicht „neu“ — benannt, nicht still übergangen
    erster: bool = False
    kritikalitaeten: Counter = field(default_factory=Counter)


def bewerte_checkmk(vorher: dict[str, str] | None, probleme: list[dict],
                    verz: Hostverzeichnis) -> CheckmkAbgleich:
    """Nur Änderungen sind Nachrichten: neues Problem oder anderer Zustand."""
    jetzt = {f"{p['host']}|{p['service']}": p["severity"] for p in probleme}
    krit = Counter((p.get("metadata") or {}).get("criticality") or "(leer)" for p in probleme)
    if vorher is None:
        return CheckmkAbgleich([], [], jetzt, erster=True, kritikalitaeten=krit)
    out = []
    for p in probleme:
        key = f"{p['host']}|{p['service']}"
        sev = p["severity"]
        if vorher.get(key) == sev:
            continue
        crit = (p.get("metadata") or {}).get("criticality") or ""
        if sev == "critical":
            rel = 3 if crit.lower() in KRITISCH else 2
            gruende = [f"{p['service']} CRIT" + (f" auf Host mit Kritikalität {crit}" if crit else "")]
        else:
            rel, gruende = 1, [f"{p['service']} {sev.upper()}"]
        gruende[0] += (" (neu)" if key not in vorher else f" (vorher {vorher[key].upper()})")
        if p.get("acknowledged"):
            rel = min(rel, 1)
            gruende.append("quittiert")
        kanon, bekannt, htags = _host_tags(p["host"], verz)
        th = [t.id for t in themen(p.get("output") or "")]
        dt = dienst_thema(p["service"])
        tags = (["quelle:checkmk", f"dienst:{p['service']}", f"zustand:{_ZUSTAND_TAG.get(sev, sev)}"]
                + htags + [f"thema:{t}" for t in ([dt] if dt else []) + th])
        out.append(Treffer("checkmk", f"dienst:{p['service']}", kanon, rel, gruende,
                           _ohne_doppel(tags), 1, (p.get("output") or "")[:500], bekannt))
    behoben = [k for k in vorher if k not in jetzt]
    return CheckmkAbgleich(out, behoben, jetzt, kritikalitaeten=krit)


# ── Quellenübergreifend ────────────────────────────────────────────────────────

def quellenuebergreifend(treffer: list[Treffer]) -> set[str]:
    """Hosts, die zugleich in mindestens zwei Quellen relevant (≥ 2) sind; deren
    relevante Treffer werden dringend (3).

    Gemeinsames Auftreten im selben Zeitraum ist KEINE belegte Ursache — der Grund sagt
    das ausdrücklich (kein post hoc).
    """
    quellen: dict[str, set[str]] = {}
    for t in treffer:
        if t.relevanz >= SPEICHERN_AB and t.host_bekannt:
            quellen.setdefault(t.host, set()).add(t.quelle)
    hosts = {h for h, q in quellen.items() if len(q) >= 2}
    for t in treffer:
        if t.host in hosts and t.relevanz >= SPEICHERN_AB and t.relevanz < 3:
            andere = sorted(quellen[t.host] - {t.quelle})
            t.relevanz = 3
            t.gruende.append(f"zugleich relevant in {', '.join(andere)} (selber Zeitraum) "
                             f"— gemeinsames Auftreten, keine Ursache belegt")
    return hosts


# ── Flutbremse ─────────────────────────────────────────────────────────────────

class Kontingent:
    """Je (Quelle, Gruppe, Host, Stunde) höchstens KONTINGENT gespeicherte Nachrichten."""

    def __init__(self, je_gruppe: int = KONTINGENT):
        self.je_gruppe = je_gruppe
        self._genutzt: dict[tuple, int] = {}

    def nimm(self, t: Treffer, stunde: datetime) -> int:
        if t.relevanz < SPEICHERN_AB:
            return 0
        key = (t.quelle, t.schluessel, t.host, stunde)
        frei = self.je_gruppe - self._genutzt.get(key, 0)
        n = max(0, min(frei, t.anzahl))
        self._genutzt[key] = self._genutzt.get(key, 0) + n
        return n

    def aufraeumen(self, vor: datetime) -> None:
        self._genutzt = {k: v for k, v in self._genutzt.items() if k[3] >= vor}


def zaehle(treffer: list[Treffer], kontingent: Kontingent, stunde: datetime) -> dict:
    """Trockenlauf-Statistik eines Fensters: was gespeichert würde und wie viel Platz."""
    je_rel = Counter()
    gruppen_je_rel = Counter()
    gespeichert = 0
    tags = Counter()
    #: nicht zugeordnete Hosts je Grund (unbekannt | mehrdeutig | container-id)
    unbekannt: dict[str, set[str]] = {}
    byte = 0
    for t in treffer:
        je_rel[t.relevanz] += t.anzahl
        gruppen_je_rel[t.relevanz] += 1
        n = kontingent.nimm(t, stunde)
        gespeichert += n
        byte += n * BYTES_JE_NACHRICHT.get(t.quelle, 1500)
        if t.relevanz >= SPEICHERN_AB:
            for h in t.hashtags:
                tags[h] += t.anzahl
        if not t.host_bekannt and t.host:
            grund = next((h.split(":", 1)[1] for h in t.hashtags if h.startswith("hostliste:")),
                         "unbekannt")
            unbekannt.setdefault(grund, set()).add(t.host)
    return {
        "nachrichten": sum(je_rel.values()),
        "je_relevanz": {str(k): je_rel.get(k, 0) for k in range(4)},
        "gruppen_je_relevanz": {str(k): gruppen_je_rel.get(k, 0) for k in range(4)},
        "gespeichert_waere": gespeichert,
        "bytes_waere": byte,
        "unbekannte_hosts": sorted(unbekannt.get("unbekannt", set()))[:50],
        "n_unbekannte_hosts": len(unbekannt.get("unbekannt", set())),
        "nicht_zugeordnet": {g: len(h) for g, h in sorted(unbekannt.items())},
        "top_hashtags": tags.most_common(25),
    }
