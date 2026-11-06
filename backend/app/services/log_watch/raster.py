"""Raster: den Stream aller drei Quellen per Skript taggen und beurteilen — ohne KI.

Ergebnis je Nachrichtengruppe (Muster × Host, Regel × Agent, Service × Host) ein
`Treffer` mit Urteil (relevant | nicht_relevant), den Kriterien der Relevanz-Definition,
auf die es sich stützt (definition.py — R1…R8, N0…N6), einer Stufe 0–3, lesbaren Gründen
und Hashtags. Hashtags haben genau eine Form, `art:wert`; sie sind zugleich Filter,
Facette und Netzplan-Knoten (Plan „Netzplan = Suche“).

Stufe: relevant → 2, dringend → 3; nicht relevant → 1, bei Rauschen/selbst erzeugt/stumm
(N4/N5/N6) → 0. In die Historie geht, was relevant ist; je Gruppe und Stunde höchstens
`KONTINGENT` Nachrichten — der Rest wird gezählt, nicht verworfen.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from app.services.log_watch.definition import (
    NICHT_RELEVANT, RELEVANT, STANDARD, Definition, entscheide,
)
from app.services.log_watch.hosts import HostEintrag, Hostverzeichnis, norm
from app.services.log_watch.vokabular import dienst_thema, themen

KONTINGENT = 5
#: Mittlere Nachrichtengröße je Quelle in Byte, für die Platzschätzung.
#: Graylog gemessen 06.11. (Stichprobe 500 Fehlerzeilen); Wazuh/CheckMK geschätzt.
BYTES_JE_NACHRICHT = {"graylog": 1468, "wazuh": 2500, "checkmk": 800}
#: Höchstens so viele Hostgruppen-Tags je Nachricht.
MAX_HG = 3
#: Themen, die ein Kriterium erfüllen
THEMEN_R5 = frozenset({"oom", "disk-full", "fs-readonly", "kernel-io", "absturz"})
THEMEN_R8 = frozenset({"cert", "backup"})
#: Fehler-Cluster-Regel → Kriterium („persistent“ = neues Muster läuft anhaltend)
REGEL_KRITERIUM = {"new": "R2", "persistent": "R2", "spread": "R3", "spike": "R4"}


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
    urteil: str = NICHT_RELEVANT
    #: entscheidende Kriterien (definition.py), z. B. ["R2", "R5"] oder ["N3"]
    kriterien: list[str] = field(default_factory=list)
    definition_v: int = 1
    #: Nachrichten-IDs der Quelle, die die Historie speichern darf (Graylog: erste je Fenster)
    belege: list = field(default_factory=list)


def stufe(urteil: str, kriterien: list[str], dringend: bool) -> int:
    if urteil == RELEVANT:
        return 3 if dringend else 2
    return 0 if set(kriterien) & {"N4", "N5", "N6"} else 1


def _urteil(quelle, schluessel, host, bekannt, r: set, n: set, dringend: bool, gruende,
            tags, anzahl, beispiel, d: Definition, belege=None) -> Treffer:
    u, k = entscheide(r, n)
    return Treffer(quelle, schluessel, host, stufe(u, k, dringend), gruende, _ohne_doppel(tags),
                   anzahl, beispiel[:500], bekannt, u, k, d.version, list(belege or []))


def _host_tags(name: str, verz: Hostverzeichnis) -> tuple[str, HostEintrag | None, list[str]]:
    e, k, grund = verz.zuordnen(name)
    kanon = e.name if e else k
    tags = [f"host:{kanon}"] if kanon else []
    if e is None:
        tags.append(f"hostliste:{grund}")
        return kanon, None, tags
    if e.ort:
        tags.append(f"ort:{e.ort}")
    if e.kritikalitaet:
        tags.append(f"kritikalitaet:{e.kritikalitaet}")
    tags += [f"hg:{g}" for g in e.hostgruppen[:MAX_HG]]
    return kanon, e, tags


def _nicht_produktion(e: HostEintrag | None, d: Definition) -> bool:
    return bool(e and e.kritikalitaet.lower() in d.p("nicht_produktion"))


def _ohne_doppel(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in tags if t and not t.endswith(":")))


def _themen_kriterien(th, r: set, gruende: list) -> None:
    for t in th:
        if t.id in THEMEN_R5 and "R5" not in r:
            r.add("R5")
            gruende.append(f"Thema „{t.text}“ (R5 Schaden an Ressourcen)")
        elif t.id in THEMEN_R8 and "R8" not in r:
            r.add("R8")
            gruende.append(f"Thema „{t.text}“ (R8 ablaufende Frist)")


# ── Graylog ────────────────────────────────────────────────────────────────────

_MELDEND = ("open", "open_digest", "reopen", "renotify")


def bewerte_graylog(templates: dict, events: list, kritisch: dict, stumm: set[str],
                    verz: Hostverzeichnis, d: Definition = STANDARD) -> list[Treffer]:
    """Ein 5-min-Fenster der Fehler-Cluster-Erkennung beurteilen.

    templates: Muster-Schlüssel → TemplateTally (mit `hosts` und `belege` je Quelle)
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
        r: set[str] = set()
        gruende = ["Fehlerzeile in Graylog"]
        sofort = False
        ev = ereignis.get(key)
        if ev is not None:
            r |= {REGEL_KRITERIUM[x] for x in ev.verdict.rules if x in REGEL_KRITERIUM}
            gruende += list(ev.verdict.reasons) or [
                "Muster auffällig: " + ", ".join(sorted(ev.verdict.rules))]
            sofort = ev.verdict.notify == "immediate"
            if sofort:
                gruende.append("Sofort-Meldung der Fehler-Cluster-Erkennung")
        _themen_kriterien(th, r, gruende)
        apps = [e for e, _ in tt.entities.most_common() if e and e not in tt.sources][:3]
        hosts = getattr(tt, "hosts", None) or Counter({s: 0 for s in tt.sources})
        belege = getattr(tt, "belege", None) or {}
        for host, n_zeilen in hosts.items():
            rr, nn, g = set(r), set(), list(gruende)
            kanon, e, htags = _host_tags(host, verz)
            sigs = krit_je_host.get(norm(host))
            if sigs:
                rr.add("R5")
                g.append("Kritische Kernelmeldung auf diesem Host: " + ", ".join(sorted(set(sigs))))
            if key in stumm:
                nn.add("N6")
                g.append("Muster stumm geschaltet (Admin)")
            if _nicht_produktion(e, d):
                nn.add("N3")
                g.append(f"Host mit Kritikalität {e.kritikalitaet}")
            if not rr:
                nn.add("N1")
            tags = (["quelle:graylog", f"muster:{key}"] + htags + [f"app:{a}" for a in apps]
                    + [f"thema:{t.id}" for t in th])
            out.append(_urteil("graylog", f"muster:{key}", kanon, e is not None, rr, nn,
                               sofort or bool(sigs), g, tags, n_zeilen or tt.count, beispiel, d,
                               belege.get(host)))
    return out


# ── Wazuh ──────────────────────────────────────────────────────────────────────

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


def bewerte_wazuh(zeilen: list[dict], verz: Hostverzeichnis,
                  d: Definition = STANDARD) -> list[Treffer]:
    out = []
    for z in zeilen:
        r: set[str] = set()
        n: set[str] = set()
        gruende = [f"Wazuh-Regel {z['regel']}, Level {z['level']}: {z['beschreibung']}"]
        if z["level"] >= d.p("wazuh_level_ab"):
            r.add("R6")
        if z["regel"] in d.p("rauschen_regeln") or set(z["gruppen"]) & set(d.p("rauschen_regelgruppen")):
            n.add("N4")
            gruende.append("bekanntes Rauschen (Regel/Regelgruppe in der Definition)")
        th = themen(z["beschreibung"])
        _themen_kriterien(th, r, gruende)
        kanon, e, htags = _host_tags(z["agent"], verz)
        if _nicht_produktion(e, d):
            n.add("N3")
            gruende.append(f"Host mit Kritikalität {e.kritikalitaet}")
        tags = (["quelle:wazuh", f"regel:{z['regel']}"] + htags
                + [f"regelgruppe:{g}" for g in z["gruppen"][:3]]
                + [f"mitre:{t}" for t in z["taktiken"]] + [f"thema:{t.id}" for t in th])
        out.append(_urteil("wazuh", f"regel:{z['regel']}", kanon, e is not None, r, n,
                           z["level"] >= d.p("wazuh_level_dringend"), gruende, tags, z["anzahl"],
                           z["beschreibung"], d))
    return out


# ── CheckMK ────────────────────────────────────────────────────────────────────

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
                    verz: Hostverzeichnis, d: Definition = STANDARD) -> CheckmkAbgleich:
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
        crit = ((p.get("metadata") or {}).get("criticality") or "").lower()
        dt = dienst_thema(p["service"])
        r: set[str] = set()
        n: set[str] = set()
        gruende = [f"{p['service']} {sev.upper()}" + (f" auf Host mit Kritikalität {crit}" if crit else "")
                   + (" (neu)" if key not in vorher else f" (vorher {vorher[key].upper()})")]
        if sev == "critical" and crit in d.p("relevante_kritikalitaeten"):
            r.add("R1")
        if sev == "critical" and dt in THEMEN_R5:
            r.add("R5")
        if dt in THEMEN_R8:
            r.add("R8")
        if p.get("acknowledged") or p.get("in_downtime"):
            n.add("N2")
            gruende.append("quittiert" if p.get("acknowledged") else "in Downtime")
        if crit in d.p("nicht_produktion"):
            n.add("N3")
        if any(p["service"].startswith(x) for x in d.p("selbst_dienste")):
            n.add("N5")
            gruende.append("vom Monitoring selbst erzeugt")
        kanon, e, htags = _host_tags(p["host"], verz)
        th = [t.id for t in themen(p.get("output") or "")]
        tags = (["quelle:checkmk", f"dienst:{p['service']}", f"zustand:{_ZUSTAND_TAG.get(sev, sev)}"]
                + htags + [f"thema:{t}" for t in ([dt] if dt else []) + th])
        out.append(_urteil("checkmk", f"dienst:{p['service']}", kanon, e is not None, r, n,
                           sev == "critical" and crit in d.p("dringende_kritikalitaeten"),
                           gruende, tags, 1, p.get("output") or "", d))
    behoben = [k for k in vorher if k not in jetzt]
    return CheckmkAbgleich(out, behoben, jetzt, kritikalitaeten=krit)


# ── Quellenübergreifend ────────────────────────────────────────────────────────

def quellenuebergreifend(treffer: list[Treffer]) -> set[str]:
    """Hosts, die zugleich in mindestens zwei Quellen relevant sind: deren relevante
    Treffer bekommen R7 und werden dringend (3).

    Gemeinsames Auftreten im selben Zeitraum ist KEINE belegte Ursache — der Grund sagt
    das ausdrücklich (kein post hoc). Nicht relevante Treffer bleiben, was sie sind.
    """
    quellen: dict[str, set[str]] = {}
    for t in treffer:
        if t.urteil == RELEVANT and t.host_bekannt:
            quellen.setdefault(t.host, set()).add(t.quelle)
    hosts = {h for h, q in quellen.items() if len(q) >= 2}
    for t in treffer:
        if t.host in hosts and t.urteil == RELEVANT and "R7" not in t.kriterien:
            andere = sorted(quellen[t.host] - {t.quelle})
            t.relevanz = 3
            t.kriterien = sorted(set(t.kriterien) | {"R7"})
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
        if t.urteil != RELEVANT:
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
    je_urteil = Counter()
    je_kriterium = Counter()
    gespeichert = 0
    tags = Counter()
    #: nicht zugeordnete Hosts je Grund (unbekannt | mehrdeutig | container-id)
    unbekannt: dict[str, set[str]] = {}
    byte = 0
    for t in treffer:
        je_rel[t.relevanz] += t.anzahl
        gruppen_je_rel[t.relevanz] += 1
        je_urteil[t.urteil] += t.anzahl
        for k in t.kriterien:
            je_kriterium[k] += t.anzahl
        n = kontingent.nimm(t, stunde)
        gespeichert += n
        byte += n * BYTES_JE_NACHRICHT.get(t.quelle, 1500)
        if t.urteil == RELEVANT:
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
        "je_urteil": dict(je_urteil),
        "je_kriterium": dict(sorted(je_kriterium.items())),
        "gespeichert_waere": gespeichert,
        "bytes_waere": byte,
        "unbekannte_hosts": sorted(unbekannt.get("unbekannt", set()))[:50],
        "n_unbekannte_hosts": len(unbekannt.get("unbekannt", set())),
        "nicht_zugeordnet": {g: len(h) for g, h in sorted(unbekannt.items())},
        "top_hashtags": tags.most_common(25),
    }
