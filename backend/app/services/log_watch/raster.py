"""Raster: den Stream aller drei Quellen per Skript taggen und beurteilen — ohne KI.

Ergebnis je Nachrichtengruppe (Muster × Host, Regel × Agent, Service × Host) ein
`Treffer` mit Hashtags (`art:wert` — zugleich Filter, Facette, Netzplan-Knoten). Was eine
Quelle beobachtet, setzt sie als Signal-Hashtag (`signal:neu`, `signal:schaden` …); das
Regelwerk (regelwerk.py, Firewall-Semantik) entscheidet daraus FORWARD (relevant) oder
BLOCK und nennt die Regel und die Kriterien der Relevanz-Definition (R1…R8, N0…N6).

Stufe: relevant → 2, dringend → 3; nicht relevant → 1, bei Rauschen/selbst erzeugt/stumm
(N4/N5/N6) → 0. In die Historie geht, was relevant ist; je Gruppe und Stunde höchstens
`KONTINGENT` Nachrichten — der Rest wird gezählt, nicht verworfen.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from app.services.log_watch.definition import NICHT_RELEVANT, RELEVANT, STANDARD, Definition
from app.services.log_watch.regelwerk import FORWARD, bewerte
from app.services.log_watch.hosts import HostEintrag, Hostverzeichnis, norm
from app.services.log_watch.vokabular import THEMEN, dienst_thema, themen

KONTINGENT = 5
#: Mittlere Nachrichtengröße je Quelle in Byte, für die Platzschätzung.
#: Graylog gemessen 06.11. (Stichprobe 500 Fehlerzeilen); Wazuh/CheckMK geschätzt.
BYTES_JE_NACHRICHT = {"graylog": 1468, "wazuh": 2500, "checkmk": 800, "firewall": 2600, "switch": 600}
#: Höchstens so viele Hostgruppen-Tags je Nachricht.
MAX_HG = 3
#: Themen, die ein Kriterium erfüllen
THEMEN_R5 = frozenset({"oom", "disk-full", "fs-readonly", "kernel-io", "absturz"})
THEMEN_R8 = frozenset({"cert", "backup"})
#: Fehler-Cluster-Regel → Signal („persistent“ = neues Muster läuft anhaltend)
REGEL_SIGNAL = {"new": "neu", "persistent": "neu", "spread": "ausbreitung", "spike": "anstieg"}
#: Anwendungen, die eine Zeile selbst ausweisen, auch ohne Container-Namen: (Ausdruck über Muster bzw. Beispiel, app)
#: Nutzer 07.11.: Ansible ist selbst erzeugt (N5, @Selbst-erzeugt). Gemessen 07.11. an 1 012 Mustern mit „ansible“:
#: Modulaufruf/-warnung auf dem Zielhost 422, sudo des Benutzers ansible 175, Laufbilanz (ansible.log) 156,
#: sshd mit Benutzer ansible 2. NICHT: Dienste auf Hosts, die „ansible“ nur im Namen tragen (certmonger auf
#: ansible-runner), sshd-Sitzungszeilen ohne Benutzer (pam_env, syslogin_perform_logout — auch Menschen).
SELBST_APPS = ((re.compile(r"\bansible-[\w.]+:? (?:Invoked with\b|\[WARNING\])"
                           r"|\bsudo\b\W{1,4}ansible : (?:TTY=|PWD=|\(command continued\))"
                           r"|\bp=\S+ (?:u=)?\S+ n=ansible \|"
                           r"|\b(?:for|from) (?:invalid )?user ansible\b|\bfor ansible from\b"), "ansible"),)


#: Zeilen, die als bekanntes Rauschen am Text erkennbar sind — über einen Hashtag statt einer Musterliste, weil Drain
#: den Hostnamen oft ins Muster nimmt (gemessen 08.11.: 46 Muster für zwei sshd-Zeilen, je neuer Host ein neues).
#: Nutzer 07.11.: sshd-Sitzungszeilen ohne Benutzer sind Rauschen (N4) — sie entstehen bei jedem SSH-Login.
TEXT_HASHTAGS = ((re.compile(r"pam_env\(sshd:session\): deprecated reading of user environment"
                             r"|syslogin_perform_logout: logout\(\) returned an error"), "rauschen:sshd-sitzung"),)


def tags_aus_text(*texte: str) -> list[str]:
    """Hashtags aus dem Zeilentext (TEXT_HASHTAGS). Rein."""
    return [tag for rx, tag in TEXT_HASHTAGS if any(rx.search(t or "") for t in texte)]


def apps_aus_text(*texte: str) -> list[str]:
    """Anwendungen aus dem Zeilentext (SELBST_APPS) — für den Hashtag app:<name>."""
    return [app for rx, app in SELBST_APPS if any(rx.search(t or "") for t in texte)]


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
    #: Kriterien (definition.py), entscheidendes zuerst, z. B. ["R5", "R1"] oder ["N3"]
    kriterien: list[str] = field(default_factory=list)
    definition_v: int = 1
    #: entscheidende Regel des Regelwerks („default“ = Default-Policy)
    regel: str = "default"
    #: aktive Regeln, die passten, aber nicht entschieden — für die Verdeckung
    verdraengt: list[str] = field(default_factory=list)
    #: Regeln im Testmodus, die gepasst hätten
    test_treffer: list[str] = field(default_factory=list)
    #: Felder, die Regeln vergleichen (filter.FELDER, z. B. level) — gehen mit in die Historie,
    #: damit Suche und Regel dasselbe Feld sehen
    felder: dict = field(default_factory=dict)
    #: so viele Nachrichten dieses Treffers darf die Historie aufnehmen (Flutbremse) — EINE
    #: Entscheidung für Zählung und Speicherung, gesetzt in zaehle()
    kontingent_n: int = 0
    #: das Relevanz-Modell hat geblockt — bleibt trotzdem in der Historie (auffindbar, messbar; nie zur KI)
    modell_entschied: bool = False
    #: Nachrichten-IDs der Quelle, die die Historie speichern darf (Graylog: erste je Fenster)
    belege: list = field(default_factory=list)


#: Ist-Zustand „behandelt“ aus Incidents (Extension, nicht Teil der versionierten Definition): je Incident
#: mit offenem Ticket (Bedingung seines Filters, Grund). Gesetzt je Lauf von lauf.py (setze_behandelt);
#: leer = nichts behandelt. Wie CheckMK-Quittung/Downtime wird daraus `signal:behandelt` → Kriterium N2.
_BEHANDELT: list[tuple[dict, str]] = []


def setze_behandelt(liste: list[tuple[dict, str]]) -> None:
    _BEHANDELT[:] = liste


def _behandelt(tags: list[str], felder: dict, gruende: list[str]) -> tuple[list[str], list[str]]:
    """Trifft der Filter eines Incidents mit offenem Ticket, ist die Nachricht behandelt (N2), mit Grund."""
    if not _BEHANDELT or "signal:behandelt" in tags:
        return tags, gruende
    from app.services.log_watch.regelwerk import passt
    ts = set(tags)
    for bed, grund in _BEHANDELT:
        if passt(bed, ts, felder or {}):
            return tags + ["signal:behandelt"], gruende + [grund]
    return tags, gruende


def _urteil(quelle, schluessel, host, bekannt, tags, felder, gruende, anzahl, beispiel,
            d: Definition, belege=None) -> Treffer:
    """Das Regelwerk entscheidet; Stufe: FORWARD 2 (dringend 3), BLOCK laut Regel 0/1."""
    tags, gruende = _behandelt(_ohne_doppel(tags), felder, list(gruende))
    e = bewerte(d.regeln, set(tags), felder)
    if e.aktion == FORWARD:
        urteil, st = RELEVANT, 3 if "signal:dringend" in tags else 2
    else:
        urteil, st = NICHT_RELEVANT, e.stufe_block
    return Treffer(quelle, schluessel, host, st, gruende, tags, anzahl, beispiel[:500], bekannt,
                   urteil=urteil, kriterien=e.kriterien, definition_v=d.version, regel=e.regel,
                   verdraengt=e.verdraengt, test_treffer=e.test_treffer, belege=list(belege or []),
                   felder=dict(felder or {}))


#: Anteil der Modell-Entscheidungen „Rauschen“, die zur Messung trotzdem relevant bleiben (Erkundung, markiert) —
#: so urteilt die KI auch über Fälle, die das Modell weggefiltert hätte, und seine Trefferquote in Produktion ist
#: messbar (Rat der vi-mcp-Sitzung: ohne Erkundung kein ehrlicher Vergleich)
ERKUNDUNG = 0.05
SIG_ENTSCHIED = "signal:modell-entschied"
SIG_ERKUNDUNG = "signal:modell-erkundung"
SIG_AUFF_ENTSCHIED = "signal:auffaellig-entschied"


def erkundung(t: Treffer, b: datetime | None) -> bool:
    """Deterministisch je (Quelle, Schlüssel, Host, Fenster) — derselbe Fall fällt immer gleich. Rein."""
    import hashlib
    h = hashlib.sha256(f"{t.quelle}|{t.schluessel}|{t.host}|{b.isoformat() if b else ''}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32 < ERKUNDUNG


def mit_modell(treffer: list[Treffer], d: Definition, b: datetime | None = None) -> list[Treffer]:
    """Gelernte Modelle als Nachlauf über ALLE Treffer eines Fensters einer Quelle — erst hier sind Breite (Hosts je
    Schlüssel) und die Fenstersumme je Muster bekannt. Nutzer 12.11.2026: „kein schattenbetrieb wir testen das in
    produktion“ — die Modelle wirken; entscheiden tut weiter das Regelwerk über ihre Signale:
    - Relevanz-Modell → `signal:modell-rauschen` (Regel N1-MOD blockt, nach R5/R6/R1 — nie gegen Schaden, Sicherheit,
      Produktionsausfall). Blockt das Modell, bleibt der Treffer mit `signal:modell-entschied` in der Historie
      (auffindbar, nie zur KI); ERKUNDUNG der Fälle bleiben relevant (`signal:modell-erkundung`) zur Messung.
    - Auffälligkeiten (IsolationForest, auffaellig.py) → `signal:auffaellig` (Regel R4-AUF leitet weiter, was sonst
      verworfen würde; dann `signal:auffaellig-entschied`).
    Jede Änderung mit Grund. Ändert die Treffer in place."""
    from app.services.log_watch import auffaellig, relevanz_modell
    if relevanz_modell.aktuelles() is None and auffaellig.aktuelles() is None:
        return treffer
    hosts: dict[tuple, set] = {}
    for t in treffer:
        if t.urteil == RELEVANT:
            hosts.setdefault((t.quelle, t.schluessel), set()).add(t.host)
    try:
        auff = auffaellig.pruefe_fenster(treffer, b) if auffaellig.aktuelles() else {}
    except Exception:  # noqa: BLE001 — ein kaputtes Modell darf das Raster nicht anhalten
        auff = {}
    for t in treffer:
        neu_tags, neu_gr = [], []
        if (t.quelle, t.schluessel) in auff:
            wert, gr = auff[(t.quelle, t.schluessel)]
            neu_tags.append(auffaellig.SIGNAL)
            neu_gr.append(f"Auffällig (IsolationForest {wert:.2f}): {'; '.join(gr) or 'ungewöhnliche Kombination'}")
        try:
            sig = relevanz_modell.pruefe(t.quelle, t.hashtags, t.felder, t.kriterien, b,
                                         breite=len(hosts.get((t.quelle, t.schluessel), ())) or None)
        except Exception:  # noqa: BLE001
            sig = None
        if sig is not None:
            neu_tags.append(relevanz_modell.SIGNAL)
            neu_gr.append(sig.grund)
        if not neu_tags:
            continue
        t.hashtags = t.hashtags + neu_tags
        t.gruende = t.gruende + neu_gr
        e2 = bewerte(d.regeln, set(t.hashtags), t.felder)
        if e2.regel == t.regel:                  # Urteil unverändert (z. B. Regel im Testmodus zählt nur)
            t.test_treffer = list(e2.test_treffer)
            continue
        modell_blockt = t.urteil == RELEVANT and e2.aktion != FORWARD and relevanz_modell.SIGNAL in t.hashtags
        if modell_blockt and erkundung(t, b):
            t.hashtags.append(SIG_ERKUNDUNG)
            t.gruende.append(f"Erkundung ({ERKUNDUNG:.0%}): Regel {e2.regel} hätte geblockt — bleibt zur Messung relevant")
            t.test_treffer = list(t.test_treffer) + [e2.regel]
            continue
        auff_leitet = t.urteil != RELEVANT and e2.aktion == FORWARD and auffaellig.SIGNAL in t.hashtags
        if e2.aktion == FORWARD:
            t.urteil, t.relevanz = RELEVANT, 3 if "signal:dringend" in t.hashtags else 2
        else:
            t.urteil, t.relevanz = NICHT_RELEVANT, e2.stufe_block
        t.kriterien, t.regel, t.verdraengt, t.test_treffer = e2.kriterien, e2.regel, e2.verdraengt, e2.test_treffer
        if modell_blockt:
            t.hashtags.append(SIG_ENTSCHIED)
            t.modell_entschied = True
        if auff_leitet:
            t.hashtags.append(SIG_AUFF_ENTSCHIED)
    return treffer


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
    if e.netz:
        tags.append(f"netz:{e.netz}")
    if e.netzbereich:
        tags.append(f"netzbereich:{e.netzbereich}")
    tags += [f"hg:{g}" for g in e.hostgruppen[:MAX_HG]]
    return kanon, e, tags


def _ohne_doppel(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in tags if t and not t.endswith(":")))


def _themen_signale(th, signale: set, gruende: list) -> None:
    for t in th:
        if t.id in THEMEN_R5 and "schaden" not in signale:
            signale.add("schaden")
            gruende.append(f"Thema „{t.text}“ (Schaden an Ressourcen)")
        elif t.id in THEMEN_R8 and "frist" not in signale:
            signale.add("frist")
            gruende.append(f"Thema „{t.text}“ (ablaufende Frist)")


def _sig(signale: set) -> list[str]:
    return [f"signal:{x}" for x in sorted(signale)]


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
    alle_themen = {t.id: t for t in THEMEN}
    for key, tt in templates.items():
        signale_muster: set[str] = set()
        gruende_muster = ["Fehlerzeile in Graylog"]
        ev = ereignis.get(key)
        if ev is not None:
            signale_muster |= {REGEL_SIGNAL[x] for x in ev.verdict.rules if x in REGEL_SIGNAL}
            gruende_muster += list(ev.verdict.reasons) or [
                "Muster auffällig: " + ", ".join(sorted(ev.verdict.rules))]
            if ev.verdict.notify == "immediate":
                signale_muster.add("dringend")
                gruende_muster.append("Sofort-Meldung der Fehler-Cluster-Erkennung")
        if key in stumm:
            signale_muster.add("stumm")
            gruende_muster.append("Muster stumm geschaltet (Admin)")
        apps = [e for e, _ in tt.entities.most_common() if e and e not in tt.sources][:3]
        apps = apps_aus_text(tt.template, *(tt.examples[:1])) + [a for a in apps if a != "ansible"]
        # Gruppen je (Host, Themen der Zeile) — collector.TemplateTally.gruppen
        for (host, themen_ids), g in (getattr(tt, "gruppen", None) or {}).items():
            th = [alle_themen[i] for i in themen_ids if i in alle_themen]
            sig, gr = set(signale_muster), list(gruende_muster)
            _themen_signale(th, sig, gr)
            kanon, e, htags = _host_tags(host, verz)
            sigs = krit_je_host.get(norm(host))
            if sigs:
                sig |= {"schaden", "dringend"}
                gr.append("Kritische Kernelmeldung auf diesem Host: " + ", ".join(sorted(set(sigs))))
            tags = (["quelle:graylog", f"muster:{key}"] + htags + [f"app:{a}" for a in apps]
                    + tags_aus_text(tt.template, *(tt.examples[:1]))
                    + [f"thema:{t.id}" for t in th] + _sig(sig))
            out.append(_urteil("graylog", f"muster:{key}", kanon, e is not None, tags, {}, gr,
                               g["anzahl"], g.get("beispiel") or (tt.examples[0] if tt.examples else ""),
                               d, g.get("belege")))
    return out


# ── Wazuh ──────────────────────────────────────────────────────────────────────

def wazuh_zeilen(aggs: dict) -> list[dict]:
    """Aggregation `regel` (terms rule.id) › `agent` (terms agent.name › `belege` top_hits) + `info`.

    `belege`: die ersten Alert-IDs der Gruppe (_id, Index) — damit holt die Historie genau diese
    Alerts, statt den Agenten aus dem kanonischen Hostnamen zurückzuraten. Aus denselben Alerts
    kommen die Merkmale je Alert: Labels des Agents, Decoder, Logquelle."""
    zeilen = []
    for rb in (aggs.get("regel") or {}).get("buckets") or []:
        hits = (((rb.get("info") or {}).get("hits") or {}).get("hits") or [{}])
        rule = ((hits[0] or {}).get("_source") or {}).get("rule") or {}
        mitre = rule.get("mitre") or {}
        for ab in (rb.get("agent") or {}).get("buckets") or []:
            bh = ((ab.get("belege") or {}).get("hits") or {}).get("hits") or []
            quellen = [h.get("_source") or {} for h in bh]
            zeilen.append({
                "regel": str(rb["key"]), "level": int(rule.get("level") or 0),
                "beschreibung": rule.get("description") or "",
                "gruppen": list(rule.get("groups") or []),
                "taktiken": list(mitre.get("tactic") or []),
                "techniken": list(mitre.get("id") or []),
                "labels": sorted({f"{k}={v}" for q in quellen for k, v in _flach_labels(
                    (q.get("agent") or {}).get("labels") or {}).items()}),
                "decoder": sorted({(q.get("decoder") or {}).get("name") for q in quellen} - {None, ""}),
                "logquelle": sorted({q.get("location") for q in quellen} - {None, ""}),
                "agent": str(ab["key"]), "anzahl": int(ab["doc_count"]),
                "belege": [(h["_id"], h.get("_index", "")) for h in bh],
            })
    return zeilen


def _flach_labels(d: dict, vor: str = "") -> dict[str, str]:
    """agent.labels kommt verschachtelt (Schlüssel mit Punkt → Objekt): zurück zu „host.group“."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flach_labels(v, f"{vor}{k}."))
        else:
            out[f"{vor}{k}"] = str(v)
    return out


def bewerte_wazuh(zeilen: list[dict], verz: Hostverzeichnis,
                  d: Definition = STANDARD) -> list[Treffer]:
    out = []
    for z in zeilen:
        signale: set[str] = set()
        gruende = [f"Wazuh-Regel {z['regel']}, Level {z['level']}: {z['beschreibung']}"]
        th = themen(z["beschreibung"])
        _themen_signale(th, signale, gruende)
        if z["level"] >= d.p("wazuh_level_dringend"):
            signale.add("dringend")
        kanon, e, htags = _host_tags(z["agent"], verz)
        tags = (["quelle:wazuh", f"regel:{z['regel']}"] + htags
                # alle Gruppen: gekürzt auf 3 fielen in 24 h u. a. recon (7 106 Alerts),
                # sql_injection (98) und attack (6) weg (gemessen 06.11.)
                + [f"regelgruppe:{g}" for g in z["gruppen"]]
                + [f"mitre:{t}" for t in z["taktiken"]]
                + [f"mitre-technik:{t}" for t in z.get("techniken", [])]
                + [f"label:{x}" for x in z.get("labels", [])]
                + [f"decoder:{x}" for x in z.get("decoder", [])]
                + [f"logquelle:{x}" for x in z.get("logquelle", [])]
                + [f"thema:{t.id}" for t in th]
                + _sig(signale))
        out.append(_urteil("wazuh", f"regel:{z['regel']}", kanon, e is not None, tags,
                           {"level": z["level"]}, gruende, z["anzahl"], z["beschreibung"], d,
                           z.get("belege")))
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


#: ab so vielen Zustandswechseln je Host|Dienst in 24 h flattert ein Dienst (gemessen 08.11.: 23 Paare ≥ 4,
#: „OMD mue performance“ 60 Wechsel — 282 Nachrichten als relevant gespeichert; ein Analyst sieht EIN Problem)
FLATTER_N = 4


def bewerte_checkmk(vorher: dict[str, str] | None, probleme: list[dict],
                    verz: Hostverzeichnis, d: Definition = STANDARD,
                    wechsel: dict[tuple[str, str], int] | None = None) -> CheckmkAbgleich:
    """Nur Änderungen sind Nachrichten: neues Problem oder anderer Zustand. `wechsel`: Zustandswechsel je
    (kanonischer Host, dienst:<service>) in 24 h aus der Historie → signal:flattern ab FLATTER_N (lauf.py)."""
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
        signale: set[str] = set()
        gruende = [f"{p['service']} {sev.upper()}" + (f" auf Host mit Kritikalität {crit}" if crit else "")
                   + (" (neu)" if key not in vorher else f" (vorher {vorher[key].upper()})")]
        # Trendalarm eines Dateisystems (lauf._tagesmuster_anreichern): im bekannten Tagesmuster kein Schaden
        tm = p.get("tagesmuster") or {}
        im_muster = bool(tm.get("erkannt"))
        if tm:
            gruende.append(tm.get("grund", ""))
        if im_muster:
            signale.add("tagesmuster")
        if sev == "critical" and dt in THEMEN_R5 and not im_muster:
            signale.add("schaden")
        if dt in THEMEN_R8:
            signale.add("frist")
        if sev == "critical" and crit in d.p("dringende_kritikalitaeten") and not im_muster:
            signale.add("dringend")
        if p.get("acknowledged") or p.get("in_downtime"):
            signale.add("behandelt")
            gruende.append("quittiert" if p.get("acknowledged") else "in Downtime")
        kanon, e, htags = _host_tags(p["host"], verz)
        w = (wechsel or {}).get((kanon, f"dienst:{p['service']}"), 0)
        if w >= FLATTER_N:
            signale.add("flattern")
            gruende.append(f"flattert: {w} Zustandswechsel in 24 h")
        th = [t.id for t in themen(p.get("output") or "")]
        tags = (["quelle:checkmk", f"dienst:{p['service']}", f"zustand:{_ZUSTAND_TAG.get(sev, sev)}"]
                + ([f"kritikalitaet:{crit}"] if crit else []) + htags
                + [f"thema:{t}" for t in ([dt] if dt else []) + th] + _sig(signale))
        out.append(_urteil("checkmk", f"dienst:{p['service']}", kanon, e is not None, tags, {},
                           gruende, 1, p.get("output") or "", d))
    behoben = [k for k in vorher if k not in jetzt]
    return CheckmkAbgleich(out, behoben, jetzt, kritikalitaeten=krit)


# ── Quellenübergreifend ────────────────────────────────────────────────────────

#: Signale, die ein Symptom benennen (nicht nur „neu“ oder „Level 7“)
SYMPTOM_SIGNALE = frozenset({"signal:schaden", "signal:anstieg", "signal:ausbreitung", "signal:frist",
                             "signal:bedrohung"})


def traegt_symptom(t: Treffer, wazuh_dringend: int = 12) -> bool:
    """Trägt der Treffer ein Symptom? Thema aus dem Vokabular, ein Symptom-Signal, CheckMK CRIT oder Wazuh ab der
    dringenden Stufe. Rein. Gemessen 08.11.: von 1 232 R7-Treffern in 24 h hatten 619 als Partner nur Wazuh-Level-7-
    Inventar (533 netstat, 550 syscheck, 510 rootcheck-Signatur) — ohne Symptom kein Zusammentreffen."""
    tags = set(t.hashtags)
    return (any(x.startswith("thema:") for x in tags) or bool(tags & SYMPTOM_SIGNALE) or "zustand:crit" in tags
            or (t.quelle == "wazuh" and int((t.felder or {}).get("level") or 0) >= wazuh_dringend))


def quellenuebergreifend(treffer: list[Treffer], wazuh_dringend: int = 12) -> set[str]:
    """Hosts, auf denen mindestens zwei Quellen zugleich einen SYMPTOMTRAGENDEN relevanten Treffer haben: diese
    Treffer bekommen R7 und werden dringend (3). Simuliert 08.11. an 24 h: 129 Host×Fenster → 11 (5 Hosts),
    übrig blieben Firewall+Graylog (web01) und CheckMK+Graylog (mon03) — was ein Analyst meint.

    Gemeinsames Auftreten im selben Zeitraum ist KEINE belegte Ursache — der Grund sagt
    das ausdrücklich (kein post hoc). Nicht relevante Treffer bleiben, was sie sind.
    """
    quellen: dict[str, set[str]] = {}
    for t in treffer:
        if t.urteil == RELEVANT and t.host_bekannt and traegt_symptom(t, wazuh_dringend):
            quellen.setdefault(t.host, set()).add(t.quelle)
    hosts = {h for h, q in quellen.items() if len(q) >= 2}
    for t in treffer:
        if (t.host in hosts and t.urteil == RELEVANT and "R7" not in t.kriterien
                and traegt_symptom(t, wazuh_dringend)):
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
        if t.urteil != RELEVANT and not t.modell_entschied:
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
    je_regel = Counter()          # Entscheidungen je Regel (Nachrichten)
    verdraengt = Counter()        # passte, entschied aber nicht (eine frühere Regel war zuerst)
    test = Counter()              # Regeln im Testmodus: hätten gepasst
    #: Relevanz-Modell hielt die Gruppe für Rauschen — je Urteil und Kriterium der Regeln (Widerspruch sichtbar)
    mod_urteil, mod_krit = Counter(), Counter()
    mod_entschied = mod_erkundung = auff_n = auff_entschied = 0
    auff_urteil = Counter()
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
        je_regel[t.regel] += t.anzahl
        for r in t.verdraengt:
            verdraengt[r] += t.anzahl
        for r in t.test_treffer:
            test[r] += t.anzahl
        if "signal:modell-rauschen" in t.hashtags:
            mod_urteil[t.urteil] += t.anzahl
            mod_krit[(t.kriterien or ["?"])[0]] += t.anzahl
            mod_entschied += t.anzahl if SIG_ENTSCHIED in t.hashtags else 0
            mod_erkundung += t.anzahl if SIG_ERKUNDUNG in t.hashtags else 0
        if "signal:auffaellig" in t.hashtags:
            auff_n += t.anzahl
            auff_urteil[t.urteil] += t.anzahl
            auff_entschied += t.anzahl if SIG_AUFF_ENTSCHIED in t.hashtags else 0
        n = kontingent.nimm(t, stunde)
        t.kontingent_n = n
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
        "je_regel": dict(sorted(je_regel.items())),
        "regel_verdraengt": dict(sorted(verdraengt.items())),
        "regel_test": dict(sorted(test.items())),
        "modell_rauschen": {"nachrichten": sum(mod_urteil.values()), "je_urteil": dict(mod_urteil),
                            "je_kriterium": dict(sorted(mod_krit.items())), "entschied": mod_entschied,
                            "erkundung": mod_erkundung},
        "auffaellig": {"nachrichten": auff_n, "je_urteil": dict(auff_urteil), "entschied": auff_entschied},
        "gespeichert_waere": gespeichert,
        "bytes_waere": byte,
        "unbekannte_hosts": sorted(unbekannt.get("unbekannt", set()))[:50],
        "n_unbekannte_hosts": len(unbekannt.get("unbekannt", set())),
        "nicht_zugeordnet": {g: len(h) for g, h in sorted(unbekannt.items())},
        "top_hashtags": tags.most_common(25),
    }
