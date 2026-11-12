"""Relevanz-Modell: lernt aus Urteilen, die NICHT von den Regeln stammen, welche weitergeleiteten Nachrichten
Rauschen sind — ein lesbares Modell (logistische Regression, ein Gewicht je Merkmal), kein Sprachmodell.

Nutzer 12.11.2026 (über die vi-mcp-Sitzung): „Ich möchte, dass in CentralStation KI auf die Fehlermeldungen
trainiert wird und welche Meldungen relevant sind und welche nur Rauschen. Derzeit machen wir das mit Regeln, aber
auf diese Regeln und die Zusammenhänge kann auch eine KI trainiert werden.“ — „Centralstation soll das gleich
implementieren. Das ist sozusagen ein Test System.“ Vorschlag: docs/vorschlag-relevanz-modell.md.

Labels (Herkunft am Label, Vorrang Mensch vor KI — nie das Urteil der Regeln, sonst lernte das Modell nur die Regeln):
- gold   Mensch hat geurteilt (herkunft „mensch“, POST /log-watch/nachrichten/{id}/urteil)
- silber KI-Urteil an der Nachricht (ai_relevanz: Beleg eines Befunds, später gesehen, bekannter Fehler)
Die Regel-Entscheidung (Kriterien) ist ein MERKMAL — an den Gewichten ist sichtbar, wo das Modell den Regeln
widerspricht. Klasse 1 = Rauschen (Relevanz ≤ 1), 0 = relevant.

Wirkung: das Raster setzt `signal:modell-rauschen` (mit den tragenden Gewichten als Grund), wenn P(Rauschen) die
Schwelle erreicht; entscheiden tut das Regelwerk über die Regel N1-MOD (raster.mit_modell). Nutzer 12.11.2026:
„kein schattenbetrieb wir testen das in produktion. Du musst nur die ergebnisse vergleichen können“ — das Modell
wirkt, sobald es trainiert ist; gemessen wird über lernstand.py (Verlauf je Training, Wirkung, Erkundung).

Modus, erschöpfend, immer mit Grund — eine GÜTEANGABE, keine Sperre: zu_wenig_daten (kein Modell, kein Signal) ·
test (Prüfungen nicht bestanden, Grund steht da) · bereit (bestanden). Prüfungen mit festen Regeln, kein LLM (ein LLM,
das Gewichte prüft, liest auch Logtexte und kann von ihnen gesteuert werden).
Verfahren wie vi-mcp learning.py / weight_audit.py (Mindestmengen, zeitlicher Holdout, Gewichte als JSON).
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

INDEX = "cs-log-modell"
SIGNAL = "signal:modell-rauschen"
MODI = ("zu_wenig_daten", "test", "bereit")
AUDIT_KLASSEN = ("dominant", "host", "stunde", "widerspricht")
#: Merkmal erst ab so vielen Vorkommen im Training (Peduzzi 1996, wie vi-mcp)
MIN_VORKOMMEN = 10
#: Labels je Klasse für ein Modell überhaupt — und Gold-Labels je Klasse für „bereit“
MIN_JE_KLASSE = 50
MIN_GOLD_JE_KLASSE = 50
SILBER_GEWICHT = 0.3
HOLDOUT_ANTEIL = 0.2
HOLDOUT_MAX = 0.5
MIN_HOLDOUT_JE_KLASSE = 20
AUC_MIN = 0.7
#: automatische Freigabe (ohne Gold) — strenger, weil die Labels von einem LLM stammen
AUC_AUTO_MIN = 0.75
#: Folge-Prüfung: CheckMK-CRIT am selben Host binnen dieser Zeit nach der Nachricht
FOLGE_FENSTER_S = 3600
MIN_FOLGE_N = 30
#: Von dem, was relevant ist, darf das Modell höchstens 1 % als Rauschen einstufen (Schwelle danach gewählt)
RECALL_RELEVANT_MIN = 0.99
L2 = 1e-3
#: Positivliste: Tag-Arten, die das Raster BEIM EINGANG aus Feldern der Quelle setzt (raster.py, firewall.py,
#: switch.py, hosts.py; thema: aus dem festen Vokabular vokabular.THEMEN). Gemessen 12.11.2026 an 22 206 Nachrichten
#: mit Urteil: 11,7 Tags je Nachricht in 98 Arten; ~70 Arten setzt die KI NACHTRÄGLICH aus Befunden („eigenes Tag-Feld“,
#: z. B. serie:, schaddomain:, ursache: an ~250 Nachrichten) — die wären ein Leck (gesetzt nach dem Urteil) und
#: stehen darum nicht hier; befund:/incident: entstehen ebenfalls erst danach. Eine Art fehlt hier → kein Merkmal,
#: gezählt in `arten_verworfen` (nichts verschwindet still).
ARTEN_EINGANG = ("quelle", "host", "muster", "dienst", "app", "level", "kritikalitaet", "ort", "netz", "hg",
                 "hostliste", "netzbereich", "zustand", "thema", "regel", "regelgruppe", "decoder", "logquelle",
                 "mitre", "mitre-technik", "switch", "firewall", "zone", "zielzone", "aktion", "schwere", "zielland",
                 "bedrohung", "gegenstelle", "ip")
#: Was die Regeln ableiten — Merkmal nur für den Vergleich Modell ↔ Regel (Gegenlauf ohne sie: `ohne_regel`)
ARTEN_REGEL = ("signal", "rauschen", "kriterium")
#: Seltenes fällt nicht weg, es sammelt sich je Art in `art:_selten` (auch für Unbekanntes beim Bewerten)
SELTEN = "_selten"
TOP_N = 10
#: Herausforderer (Rat der vi-mcp-Sitzung 12.11.2026, Nutzer: „ja“): bemängelte Gewichte sind ein Hinweis auf fehlende
#: Merkmale, nicht auf falsche Zahlen — darum ändert keine Variante Gewichte, sondern Merkmale; jede wird trainiert und
#: auf dem gruppierten Holdout gegen „basis“ gemessen. Name → (Skala je Merkmalsart, Zusatz-Merkmal aus Beobachtung.extra)
VARIANTEN: dict[str, tuple[dict, str | None]] = {
    "basis": ({}, None),
    "host_gedaempft": ({"host": 0.3}, None),     # Host-Merkmale mit Wert 0,3: bei L2 wie ein kleineres C für Hosts
    "job_fenster": ({}, "job_fenster"),           # Muster kommt an ≥ JOB_TAGE Tagen zur selben Stunde (Job, nicht Uhrzeit)
    "auffaellig": ({}, "auffaellig"),             # Stufe des IsolationForest-Werts (auffaellig.py) als Merkmal
}
JOB_TAGE = 3
JOB_STUNDEN_JE_TAG_MAX = 4


# ── Merkmale ──────────────────────────────────────────────────────────────────────────────────────────────
def art(merkmal: str) -> str:
    return merkmal.split(":", 1)[0]


def breite_stufe(n: int | None) -> str | None:
    """Wie viele Hosts dasselbe Muster im selben Fenster zeigen — logarithmisch gestuft. Trägt die Episode (eine Welle
    auf 51 Hosts), die sonst als Uhrzeit-Gewicht erschiene (stunde:13 +1,57 am 12.11. — Rat der vi-mcp-Sitzung). Rein."""
    if not n or n < 1:
        return None
    return "1" if n == 1 else "2-3" if n <= 3 else "4-15" if n <= 15 else "16+"


def merkmale(quelle: str, tags, level=None, kriterien=None, stunde: int | None = None, *,
             mit_regel: bool = True, nachtraeglich: set[str] | frozenset = frozenset(),
             breite: int | None = None) -> list[str]:
    """Merkmale einer Nachrichtengruppe — dieselbe Funktion im Training und im Raster. Nur Arten der Positivliste;
    `nachtraeglich` = Tags, die laut Geschichte später jemand hinzugefügt hat (nie Merkmal). Rein."""
    erlaubt = ARTEN_EINGANG + (ARTEN_REGEL if mit_regel else ())
    m = {f"quelle:{quelle}"} if quelle else set()
    m |= {t for t in tags or [] if ":" in t and art(t) in erlaubt and t not in nachtraeglich
          and not t.startswith("signal:modell")}
    try:
        if level is not None and str(level) != "":
            m.add(f"level:{int(float(level))}")
    except (TypeError, ValueError):
        pass
    # R7 setzt die Historie erst nachträglich (markiere_r7) — im Raster unbekannt, darum nie Merkmal
    if mit_regel:
        m |= {f"kriterium:{k}" for k in kriterien or [] if k != "R7"}
    if stunde is not None:
        m.add(f"stunde:{int(stunde):02d}")
    if (b := breite_stufe(breite)) is not None:
        m.add(f"breite:{b}")
    return sorted(m)


def label(doc: dict) -> tuple[int, str] | None:
    """(y, herkunft) — y = 1 Rauschen, 0 relevant; None = ohne unabhängiges Urteil. Rein."""
    if doc.get("herkunft") == "mensch":
        rel = doc.get("relevanz")
        if rel is None:
            return None
        return (1 if int(rel) <= 1 else 0), "gold"
    ai = doc.get("ai_relevanz")
    if ai is None or ai == "":
        return None
    return (1 if int(ai) <= 1 else 0), "silber"


def nachtraeglich(aenderungen) -> set[str]:
    """Tags, die laut Geschichte der Nachricht nachträglich hinzugefügt wurden („+art:wert“ von KI/Mensch). Rein."""
    import re
    out: set[str] = set()
    for e in aenderungen or []:
        if isinstance(e, dict):
            out |= set(re.findall(r"\+([\w-]+:\S+)", str(e.get("was") or "")))
    return out


def _stunde(zeit) -> int | None:
    try:
        return datetime.fromisoformat(str(zeit).replace("Z", "+00:00")).astimezone(timezone.utc).hour
    except (TypeError, ValueError):
        return None


@dataclass
class Beobachtung:
    id: str
    zeit: str
    y: int
    herkunft: str                 # gold | silber
    merkmale: list[str]
    schluessel: str = ""
    urteil_regel: str = ""        # relevant | nicht_relevant (Urteil der Regeln bzw. des Menschen)
    kriterium: str = ""           # entscheidendes Kriterium
    text: str = ""
    #: dieselbe Beobachtung ohne Regel-Merkmale (Gegenlauf: wie viel trägt die Regel?)
    merkmale_ohne_regel: list[str] | None = None
    #: Tag-Arten der Nachricht, die nicht auf der Positivliste stehen (gezählt, nie Merkmal)
    verworfen_arten: list[str] | None = None
    #: Urteil der GELTENDEN Regeln über dieselben Eingangs-Tags (1 = BLOCK) — Vergleich auf Gold-Fällen, deren
    #: gespeichertes Urteil der Mensch überschrieben hat
    regel_rauschen: int | None = None
    #: folgte binnen FOLGE_FENSTER ein CheckMK-CRIT am selben Host? (None = nicht prüfbar, z. B. CheckMK selbst)
    folge: int | None = None
    #: Zusatz-Merkmale der Herausforderer (VARIANTEN), z. B. {"job_fenster": ["job:fenster"]}
    extra: dict | None = None


def beobachtung(id_: str, doc: dict, breite: int | None = None, regel_rauschen: int | None = None
                ) -> Beobachtung | None:
    """Ein gespeichertes Dokument aus cs-logs-* als Trainingsbeispiel — oder None (KI-Befund, ohne Label). Rein."""
    if doc.get("quelle") == "ki":
        return None
    lb = label(doc)
    if lb is None:
        return None
    y, herkunft = lb
    krit = list(doc.get("kriterien") or [])
    # Bei einem Menschen-Urteil stehen dort SEINE Kriterien, nicht die der Regel → nicht als Merkmal (sonst Leck)
    spaeter = nachtraeglich(doc.get("aenderungen"))
    args = (doc.get("quelle") or "", doc.get("hashtags") or [], doc.get("level"),
            [] if herkunft == "gold" else krit, _stunde(doc.get("zeit")))
    return Beobachtung(id_, str(doc.get("zeit") or ""), y, herkunft,
                       merkmale(*args, nachtraeglich=spaeter, breite=breite),
                       str(doc.get("schluessel") or ""), str(doc.get("urteil") or ""), krit[0] if krit else "",
                       str(doc.get("text") or "")[:200],
                       merkmale_ohne_regel=merkmale(*args, mit_regel=False, nachtraeglich=spaeter, breite=breite),
                       regel_rauschen=regel_rauschen,
                       verworfen_arten=sorted({art(t) for t in doc.get("hashtags") or [] if ":" in t}
                              - set(ARTEN_EINGANG) - set(ARTEN_REGEL)))


# ── Kennzahlen ────────────────────────────────────────────────────────────────────────────────────────────
def auc(scores: list[float], y: list[int]) -> float | None:
    """ROC-AUC (Mann-Whitney, Gleichstände halb). None, wenn eine Klasse fehlt. Rein."""
    paare = sorted(zip(scores, y))
    n1 = sum(y)
    n0 = len(y) - n1
    if not n1 or not n0:
        return None
    rang_summe, i = 0.0, 0
    while i < len(paare):
        j = i
        while j + 1 < len(paare) and paare[j + 1][0] == paare[i][0]:
            j += 1
        mittel = (i + j) / 2 + 1
        rang_summe += mittel * sum(1 for k in range(i, j + 1) if paare[k][1] == 1)
        i = j + 1
    return (rang_summe - n1 * (n1 + 1) / 2) / (n1 * n0)


def schwelle_fuer(p_relevant: list[float], recall_min: float = RECALL_RELEVANT_MIN) -> float:
    """Kleinste Schwelle, bei der höchstens (1 − recall_min) der Relevanten darüber liegen. Rein."""
    if not p_relevant:
        return 1.0
    s = sorted(p_relevant)
    k = min(len(s) - 1, math.ceil(recall_min * len(s)) - 1)
    return min(1.0, s[k] + 1e-9)


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))


def abbilde(m: list[str], bekannt) -> list[str]:
    """Merkmale auf das Vokabular: Unbekanntes/Seltenes → `art:_selten`, wenn es das gibt. Rein."""
    return sorted({x if x in bekannt else f"{art(x)}:{SELTEN}" for x in m} & set(bekannt))


def _wert(skala: dict | None, merkmal: str) -> float:
    return (skala or {}).get(art(merkmal), 1.0)


def wahrscheinlichkeit(modell: dict, m: list[str]) -> float:
    g, sk = modell.get("gewichte") or {}, modell.get("skala")
    return _sig(modell.get("bias", 0.0) + sum(g.get(x, 0.0) * _wert(sk, x) for x in abbilde(m, g)))


def tragend(modell: dict, m: list[str], n: int = 3) -> list[tuple[str, float]]:
    """Die Beiträge (Gewicht × Wert), die das Urteil tragen — größter Richtung Rauschen zuerst. Rein."""
    g, sk = modell.get("gewichte") or {}, modell.get("skala")
    return sorted(((x, round(g[x] * _wert(sk, x), 4)) for x in abbilde(m, g)), key=lambda t: -t[1])[:n]


def job_merkmal(jobs: set[str] | list[str] | None, schluessel: str | None, stunde: int | None) -> list[str]:
    """`job:fenster`, wenn das Muster an ≥ JOB_TAGE Tagen zu dieser Stunde auftrat (jobs = {"key|h"}). Rein."""
    if not jobs or not schluessel or not schluessel.startswith("muster:") or stunde is None:
        return []
    return ["job:fenster"] if f"{schluessel[7:]}|{int(stunde)}" in jobs else []


def auff_stufe(wert: float | None, grenzen: dict | None) -> list[str]:
    """Stufe des IsolationForest-Werts als Merkmal: hoch (≥ Ausreißer-Schwelle) · erhoeht (≥ 90-%-Quantil) · normal. Rein."""
    if wert is None or not grenzen:
        return []
    if wert >= grenzen.get("hoch", 1e9):
        return ["auffaellig:hoch"]
    return ["auffaellig:erhoeht"] if wert >= grenzen.get("erhoeht", 1e9) else ["auffaellig:normal"]


def variante_merkmale(modell: dict, schluessel: str | None, stunde: int | None, auff_wert: float | None) -> list[str]:
    """Die Zusatz-Merkmale der gewählten Variante — dieselben Funktionen wie im Training. Rein."""
    v = modell.get("variante") or "basis"
    if v == "job_fenster":
        return job_merkmal(set(modell.get("jobs") or []), schluessel, stunde)
    if v == "auffaellig":
        return auff_stufe(auff_wert, modell.get("auff_grenzen"))
    return []


def logloss(p: list[float], y: list[int]) -> float | None:
    if not y:
        return None
    return sum(-(math.log(max(q, 1e-9)) if t else math.log(max(1 - q, 1e-9))) for q, t in zip(p, y)) / len(y)


def waehle(ergebnisse: dict[str, dict]) -> tuple[str, str]:
    """(Sieger, Grund) — ein Herausforderer gewinnt nur, wenn AUC (gruppiert) nicht schlechter UND log loss nicht höher
    als bei „basis“, und er in einem davon besser ist; unter mehreren der mit der besten AUC. Sonst bleibt „basis“. Rein."""
    b = ergebnisse.get("basis") or {}
    if b.get("auc") is None:
        return "basis", "basis ohne Kennzahlen — kein Vergleich möglich"
    gut = [(n, e) for n, e in ergebnisse.items() if n != "basis" and e.get("auc") is not None
           and e["auc"] >= b["auc"] and e["logloss"] <= b["logloss"]
           and (e["auc"] > b["auc"] or e["logloss"] < b["logloss"])]
    if not gut:
        return "basis", "kein Herausforderer besser auf ungesehenen Schlüsseln"
    n, e = max(gut, key=lambda t: (t[1]["auc"], -t[1]["logloss"]))
    return n, (f"{n}: AUC {e['auc']:.4f} ≥ {b['auc']:.4f}, log loss {e['logloss']:.4f} ≤ {b['logloss']:.4f} "
               f"(gruppierter Holdout)")


# ── Training ──────────────────────────────────────────────────────────────────────────────────────────────
def _fit(zeilen: list[list[int]], y: list[int], gew: list[float], nf: int, schritte: int = 600,
         werte: list[float] | None = None):
    """Logistische Regression mit L2, volle Gradienten mit Adam — dünn besetzte Merkmale (Wert 1, oder je Spalte
    `werte`, z. B. 0,3 für gedämpfte Arten). numpy."""
    import numpy as np
    rows = np.fromiter((i for i, r in enumerate(zeilen) for _ in r), dtype=np.int64)
    cols = np.fromiter((c for r in zeilen for c in r), dtype=np.int64)
    vals = np.ones(len(cols)) if werte is None else np.asarray(werte, dtype=float)[cols]
    yv, sw = np.asarray(y, dtype=float), np.asarray(gew, dtype=float)
    n = len(zeilen)
    w, b = np.zeros(nf), 0.0
    basis = float((yv * sw).sum() / sw.sum())
    b = math.log(max(basis, 1e-6) / max(1 - basis, 1e-6))
    m_w, v_w, m_b, v_b = np.zeros(nf), np.zeros(nf), 0.0, 0.0
    lr, b1, b2, eps = 0.05, 0.9, 0.999, 1e-8
    for t in range(1, schritte + 1):
        z = np.bincount(rows, weights=w[cols] * vals, minlength=n) + b
        p = 1 / (1 + np.exp(-np.clip(z, -35, 35)))
        r = (p - yv) * sw / sw.sum()
        g_w = np.bincount(cols, weights=r[rows] * vals, minlength=nf) + L2 * w
        g_b = float(r.sum())
        m_w = b1 * m_w + (1 - b1) * g_w
        v_w = b2 * v_w + (1 - b2) * g_w ** 2
        m_b = b1 * m_b + (1 - b1) * g_b
        v_b = b2 * v_b + (1 - b2) * g_b ** 2
        w -= lr * (m_w / (1 - b1 ** t)) / (np.sqrt(v_w / (1 - b2 ** t)) + eps)
        b -= lr * (m_b / (1 - b1 ** t)) / (math.sqrt(v_b / (1 - b2 ** t)) + eps)
    return [float(x) for x in w], float(b)


def teile(beob: list[Beobachtung]) -> tuple[list[Beobachtung], list[Beobachtung], list[Beobachtung]]:
    """(Training, Holdout zeitlich, Holdout gruppiert). Training = älterer Teil; Holdout = jüngster Teil; GRUPPIERT =
    davon nur Schlüssel, die im Training nie vorkamen — misst Verallgemeinerung statt Wiedererkennen eines
    KI-Urteils, das an Wiederholungen kopiert ist (Rat der vi-mcp-Sitzung 12.11.). Der Holdout wächst bis
    HOLDOUT_MAX, bis der gruppierte je Klasse genug Fälle hat. Rein."""
    s = sorted(beob, key=lambda b: b.zeit)
    anteil = HOLDOUT_ANTEIL
    while True:
        k = int(len(s) * (1 - anteil))
        train, ho = s[:k], s[k:]
        bekannt = {b.schluessel for b in train}
        gr = [b for b in ho if b.schluessel not in bekannt]
        n1 = sum(b.y for b in gr)
        if (n1 >= MIN_HOLDOUT_JE_KLASSE and len(gr) - n1 >= MIN_HOLDOUT_JE_KLASSE) or anteil >= HOLDOUT_MAX:
            return train, ho, gr
        anteil = min(HOLDOUT_MAX, anteil + 0.05)


def auc_gruppen(p: list[float], beob: list[Beobachtung]) -> float | None:
    """AUC je Schlüssel (mittleres P je Schlüssel und Label) — jede unabhängige Entscheidung zählt einmal. Rein."""
    gr: dict[tuple, list[float]] = {}
    for q, b in zip(p, beob):
        gr.setdefault((b.schluessel or b.id, b.y), []).append(q)
    return auc([sum(v) / len(v) for v in gr.values()], [k[1] for k in gr])


def audit(gewichte: dict[str, float], train: list[Beobachtung], ho_positiv: list[list[str]]) -> list[dict]:
    """Gewichts-Audit mit festen Regeln (AUDIT_KLASSEN). Jeder Befund hält das Modell im Test. Rein."""
    befunde: list[dict] = []
    top = sorted(gewichte.items(), key=lambda t: -abs(t[1]))[:TOP_N]
    for f, w in top:
        if f.startswith("host:"):
            befunde.append({"klasse": "host", "merkmal": f, "gewicht": round(w, 3),
                            "grund": "ein einzelner Host unter den stärksten Gewichten — gelernt wird ein Hostzustand, "
                                     "kein Muster"})
        if f.startswith("stunde:"):
            befunde.append({"klasse": "stunde", "merkmal": f, "gewicht": round(w, 3),
                            "grund": "Uhrzeit unter den stärksten Gewichten — Uhrzeit als Ursache wäre post hoc"})
    # dominant: ein Merkmal trägt die meisten Rauschen-Urteile im Holdout allein (größter Beitrag)
    if len(ho_positiv) >= MIN_HOLDOUT_JE_KLASSE:
        erste = Counter(max(m, key=lambda x: gewichte.get(x, 0.0)) for m in ho_positiv if m)
        if erste:
            f, n = erste.most_common(1)[0]
            if n / len(ho_positiv) >= 0.5:
                befunde.append({"klasse": "dominant", "merkmal": f, "gewicht": round(gewichte.get(f, 0.0), 3),
                                "anteil": round(n / len(ho_positiv), 3),
                                "grund": f"trägt {n} von {len(ho_positiv)} Rauschen-Urteilen im Holdout als stärkstes "
                                         f"Gewicht"})
    # widerspricht: Vorzeichen eines starken Gewichts gegen die gemessene Häufigkeit (Rand) im Training
    if train:
        basis = sum(b.y for b in train) / len(train)
        n_f, y_f = Counter(), Counter()
        for b in train:
            for x in b.merkmale:
                n_f[x] += 1
                y_f[x] += b.y
        for f, w in top:
            if n_f[f] >= 30 and abs(w) >= 0.5:
                diff = y_f[f] / n_f[f] - basis
                if abs(diff) >= 0.05 and (diff > 0) != (w > 0):
                    befunde.append({"klasse": "widerspricht", "merkmal": f, "gewicht": round(w, 3),
                                    "rauschen_anteil": round(y_f[f] / n_f[f], 3), "basis": round(basis, 3),
                                    "grund": f"Gewicht {w:+.2f}, aber Rauschen-Anteil mit diesem Merkmal "
                                             f"{y_f[f] / n_f[f]:.0%} gegen {basis:.0%} gesamt (n = {n_f[f]})"})
    return befunde


def modus(labels: dict, kennzahlen: dict, befunde: list[dict]) -> tuple[str, str]:
    """(modus, grund) — erschöpfend; der erste fehlende Punkt ist der Grund. Rein.

    Nutzer 12.11.2026: „es soll gleich trainieren ich muss da nichts mehr machen“ — die Freigabe darf nicht an
    Menschen-Urteilen hängen. Gibt es ≥ MIN_GOLD_JE_KLASSE Gold-Urteile je Klasse, wird gegen Gold geprüft (und gegen
    die Regeln auf denselben Fällen). Sonst AUTOMATISCH, unabhängig vom LLM, das die Silber-Labels liefert:
    gruppierter Holdout (Schlüssel nie im Training) AUC ≥ AUC_AUTO_MIN, Recall relevant ≥ RECALL_RELEVANT_MIN, und
    die FOLGE-Prüfung: auf Nachrichten, die das Modell Rauschen nennt, folgt nicht öfter ein CheckMK-CRIT am selben
    Host binnen FOLGE_FENSTER als auf die, die es relevant nennt (CheckMK ist ein vom LLM unabhängiges System).
    Immer: Gewichts-Audit ohne Befund."""
    gesamt = labels.get("gesamt") or {}
    if min(gesamt.get("rauschen", 0), gesamt.get("relevant", 0)) < MIN_JE_KLASSE:
        return "zu_wenig_daten", (f"Labels je Klasse: rauschen {gesamt.get('rauschen', 0)}, relevant "
                                  f"{gesamt.get('relevant', 0)} — nötig je {MIN_JE_KLASSE}")
    gold = labels.get("gold") or {}
    if min(gold.get("rauschen", 0), gold.get("relevant", 0)) >= MIN_GOLD_JE_KLASSE:
        a = kennzahlen.get("auc_gold")
        if a is None or a < AUC_MIN:
            return "test", f"Holdout-AUC (ungesehene Schlüssel) gegen Gold {a if a is None else round(a, 3)} < {AUC_MIN}"
        ar = kennzahlen.get("auc_regel_gold")
        if ar is not None and a <= ar:
            return "test", f"auf denselben Gold-Fällen nicht besser als die Regeln: AUC {a:.3f} gegen {ar:.3f}"
        r = kennzahlen.get("recall_relevant_gold")
        if r is None or r < RECALL_RELEVANT_MIN:
            return "test", f"Recall relevant (Gold, Holdout) {r if r is None else round(r, 3)} < {RECALL_RELEVANT_MIN}"
        basis = f"gegen Gold: AUC {a:.3f}, Recall relevant {r:.3f}"
    else:
        a = kennzahlen.get("auc")
        if a is None or a < AUC_AUTO_MIN:
            return "test", (f"automatische Prüfung: AUC auf ungesehenen Schlüsseln "
                            f"{a if a is None else round(a, 3)} < {AUC_AUTO_MIN}")
        r = kennzahlen.get("recall_relevant")
        if r is None or r < RECALL_RELEVANT_MIN:
            return "test", (f"automatische Prüfung: Recall relevant {r if r is None else round(r, 3)} < "
                            f"{RECALL_RELEVANT_MIN}")
        fr, fv, n = kennzahlen.get("folge_rauschen"), kennzahlen.get("folge_relevant"), kennzahlen.get("folge_rauschen_n") or 0
        if fr is None or fv is None or n < MIN_FOLGE_N:
            return "test", (f"automatische Prüfung: zu wenige Rauschen-Urteile mit CheckMK-Folgeprüfung ({n}, nötig "
                            f"{MIN_FOLGE_N})")
        if fr > fv:
            return "test", (f"automatische Prüfung: auf „Rauschen“ folgte öfter ein CheckMK-CRIT am Host "
                            f"({fr:.1%}) als auf „relevant“ ({fv:.1%})")
        basis = (f"automatisch: AUC {a:.3f}, Recall relevant {r:.3f}, CheckMK-CRIT danach bei Rauschen {fr:.1%} "
                 f"≤ relevant {fv:.1%}")
    if befunde:
        return "test", "Gewichts-Audit: " + "; ".join(f"{b['klasse']} {b['merkmal']}" for b in befunde[:4])
    return "bereit", f"{basis}; Audit ohne Befund"


def _zaehle_labels(beob: list[Beobachtung]) -> dict:
    out = {"gesamt": Counter(), "gold": Counter(), "silber": Counter()}
    schl = {"rauschen": set(), "relevant": set()}
    for b in beob:
        k = "rauschen" if b.y else "relevant"
        out["gesamt"][k] += 1
        out[b.herkunft][k] += 1
        if b.schluessel:
            schl[k].add(b.schluessel)
    r = {a: {"rauschen": c.get("rauschen", 0), "relevant": c.get("relevant", 0)} for a, c in out.items()}
    # viele Silber-Labels sind Kopien desselben KI-Urteils an Wiederholungen — die Zahl der Schlüssel ist ehrlicher
    r["schluessel"] = {k: len(v) for k, v in schl.items()}
    return r


def trainiere(beob: list[Beobachtung], jetzt: datetime | None = None) -> dict:
    """Ein Modell aus Beobachtungen — Gewichte, Schwelle, Kennzahlen, Audit, Widersprüche, Modus mit Grund."""
    jetzt = jetzt or datetime.now(timezone.utc)
    labels = _zaehle_labels(beob)
    leer = {"zeit": jetzt.isoformat(), "labels": labels, "gewichte": {}, "bias": 0.0, "schwelle": 1.0,
            "kennzahlen": {}, "audit": [], "widersprueche": {}, "staerkste": []}
    if min(labels["gesamt"]["rauschen"], labels["gesamt"]["relevant"]) < MIN_JE_KLASSE:
        leer["modus"], leer["grund"] = modus(labels, {}, [])
        return leer
    train, ho, gr = teile(beob)

    def mk_von(name: str):
        extra = VARIANTEN[name][1]
        return (lambda b: b.merkmale + ((b.extra or {}).get(extra) or [])) if extra else (lambda b: b.merkmale)
    # Herausforderer: jede Variante trainieren, auf dem gruppierten Holdout messen (Champion gegen Herausforderer)
    ergebnisse: dict[str, dict] = {}
    gelernt: dict[str, tuple] = {}
    for name, (skala, extra) in VARIANTEN.items():
        if extra and not any((b.extra or {}).get(extra) for b in beob):
            ergebnisse[name] = {"uebersprungen": f"keine Daten für das Merkmal {extra}"}
            continue
        mk_v = mk_von(name)
        mod_v, vok_v, zaehl_v = _lerne(train, mk_v, skala)
        p_gr_v = [wahrscheinlichkeit(mod_v, mk_v(b)) for b in gr]
        y_gr = [b.y for b in gr]
        ergebnisse[name] = {"auc": None if not gr else auc(p_gr_v, y_gr), "logloss": logloss(p_gr_v, y_gr),
                            "merkmale": len(vok_v)}
        ergebnisse[name] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ergebnisse[name].items()}
        gelernt[name] = (mod_v, vok_v, zaehl_v)
    sieger, sieger_grund = waehle(ergebnisse)
    mk = mk_von(sieger)
    modell, vok, zaehl = gelernt[sieger]
    modell = {**modell, "variante": sieger}
    p_train = [wahrscheinlichkeit(modell, mk(b)) for b in train]
    schwelle = schwelle_fuer([p for p, b in zip(p_train, train) if b.y == 0])
    p_ho = [wahrscheinlichkeit(modell, mk(b)) for b in ho]
    p_von = {b.id: q for b, q in zip(ho, p_ho)}

    def kennz(menge: list[Beobachtung]):
        if not menge:
            return None, None, None, 0
        ps, ys = [p_von[b.id] for b in menge], [b.y for b in menge]
        rel = [p for p, y in zip(ps, ys) if y == 0]
        rau = [p for p, y in zip(ps, ys) if y == 1]
        return (auc(ps, ys), (sum(1 for p in rel if p < schwelle) / len(rel)) if rel else None,
                (sum(1 for p in rau if p >= schwelle) / len(rau)) if rau else None, len(menge))

    # Maßstab: der GRUPPIERTE Holdout (Schlüssel nie im Training); der zeitliche steht daneben
    a, rr, re_, n = kennz(gr)
    a_z, rr_z, re_z, n_z = kennz(ho)
    # die Metrik ohne den größten Rauschen-Schlüssel — trägt einer allein die Hälfte, ist sie zur Hälfte ein Fall
    groesster = Counter(b.schluessel for b in ho if b.y).most_common(1)
    g_schl = groesster[0][0] if groesster else None
    a_z_ohne = kennz([b for b in ho if b.schluessel != g_schl])[0]
    # Gegenlauf ohne Regel-Merkmale: liegt die AUC kaum darunter, lernt das Modell nicht bloß die Regeln nach
    ohne, _, _ = _lerne(train, lambda b: b.merkmale_ohne_regel or [])
    a_ohne = auc([wahrscheinlichkeit(ohne, b.merkmale_ohne_regel or []) for b in gr], [b.y for b in gr])
    a_ohne_z = auc([wahrscheinlichkeit(ohne, b.merkmale_ohne_regel or []) for b in ho], [b.y for b in ho])
    gold = [b for b in gr if b.herkunft == "gold"]
    a_g, rr_g, re_g, n_g = kennz(gold)
    regel_gold = [b for b in gold if b.regel_rauschen is not None]
    a_rg = auc([float(b.regel_rauschen) for b in regel_gold], [b.y for b in regel_gold]) if regel_gold else None
    mit_folge = [b for b in gr if b.folge is not None]
    f_r = [b.folge for b in mit_folge if p_von[b.id] >= schwelle]
    f_v = [b.folge for b in mit_folge if p_von[b.id] < schwelle]
    kz = {"auc": a, "auc_gruppen": auc_gruppen([p_von[b.id] for b in gr], gr),
          "folge_rauschen": (sum(f_r) / len(f_r)) if f_r else None, "folge_rauschen_n": len(f_r),
          "folge_relevant": (sum(f_v) / len(f_v)) if f_v else None, "folge_relevant_n": len(f_v),
          "recall_relevant": rr, "rauschen_erkannt": re_, "holdout_n": n,
          "holdout_schluessel": len({b.schluessel for b in gr}),
          "auc_zeitlich": a_z, "recall_relevant_zeitlich": rr_z, "rauschen_erkannt_zeitlich": re_z,
          "holdout_zeitlich_n": n_z, "groesster_rauschen_schluessel": g_schl,
          "auc_zeitlich_ohne_groessten": a_z_ohne,
          "holdout_von": ho[0].zeit if ho else None, "holdout_bis": ho[-1].zeit if ho else None,
          "train_n": len(train), "merkmale": len(vok), "merkmale_selten": len(zaehl) - len(vok),
          "auc_ohne_regel_merkmale": a_ohne, "auc_zeitlich_ohne_regel_merkmale": a_ohne_z,
          "auc_gold": a_g, "recall_relevant_gold": rr_g, "rauschen_erkannt_gold": re_g, "holdout_gold_n": n_g,
          "auc_regel_gold": a_rg,
          "basisrate_rauschen": round(sum(b.y for b in train) / len(train), 4)}
    kz = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in kz.items()}
    # Audit und Anzeige auf dem WIRKSAMEN Beitrag (Gewicht × Wert der Merkmalsart) — bei gedämpften Hosts zählt 0,3 × w
    wirksam = {f: round(g * _wert(modell.get("skala"), f), 4) for f, g in modell["gewichte"].items()}
    befunde = audit(wirksam, train,
                    [abbilde(mk(b), modell["gewichte"]) for p, b in zip(p_ho, ho) if p >= schwelle])
    # ohne den dominanten Schlüssel (Audit „dominant“ auf ein muster:/regel:-Merkmal): wie gut ohne diesen einen Fall?
    dom = next((x["merkmal"] for x in befunde if x["klasse"] == "dominant"), None)
    if dom:
        kz["auc_ohne_dominantes"] = kennz([b for b in gr if dom not in mk(b)])[0]
        kz["auc_zeitlich_ohne_dominantes"] = kennz([b for b in ho if dom not in mk(b)])[0]
    kz = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in kz.items()}
    m, grund = modus(labels, kz, befunde)
    alle_p = p_train + p_ho
    arten = Counter(art(x) for b in train for x in mk(b))
    return {"zeit": jetzt.isoformat(), "modus": m, "grund": grund, "labels": labels, **modell,
            "varianten": ergebnisse, "variante_grund": sieger_grund,
            "merkmal_arten": dict(arten.most_common()),
            "arten_verworfen": dict(Counter(a for b in beob for a in b.verworfen_arten or []).most_common()),
            "schwelle": round(schwelle, 4), "kennzahlen": kz, "audit": befunde,
            "staerkste": [{"merkmal": f, "gewicht": g, "roh": modell["gewichte"][f]} for f, g in
                          sorted(wirksam.items(), key=lambda t: -abs(t[1]))[:25]],
            "widersprueche": widersprueche(train + ho, alle_p, schwelle)}


def _lerne(train: list[Beobachtung], mk, skala: dict | None = None) -> tuple[dict, list[str], Counter]:
    """Vokabular (Seltenes → art:_selten), Fit, Gewichte. Rein bis auf numpy."""
    zaehl = Counter(x for b in train for x in set(mk(b)))
    haeufig = {f for f, n in zaehl.items() if n >= MIN_VORKOMMEN}
    selten_arten = {art(f) for f, n in zaehl.items() if n < MIN_VORKOMMEN}
    vok = sorted(haeufig | {f"{a}:{SELTEN}" for a in selten_arten})
    idx = {f: i for i, f in enumerate(vok)}
    zeilen = [[idx[x] for x in abbilde(mk(b), idx)] for b in train]
    # jede unabhängige Entscheidung zählt einmal: ein KI-Urteil, an 500 Wiederholungen kopiert, hat nicht 500 Stimmen
    je = Counter((b.herkunft, b.schluessel or b.id, b.y) for b in train)
    gew = [(1.0 if b.herkunft == "gold" else SILBER_GEWICHT) / je[(b.herkunft, b.schluessel or b.id, b.y)]
           for b in train]
    w, bias = _fit(zeilen, [b.y for b in train], gew, len(vok), werte=[_wert(skala, f) for f in vok])
    return ({"gewichte": {f: round(w[i], 4) for f, i in idx.items() if abs(w[i]) >= 1e-3}, "bias": round(bias, 4),
             "skala": dict(skala or {})}, vok, zaehl)


def widersprueche(beob: list[Beobachtung], p: list[float], schwelle: float, n: int = 15) -> dict:
    """Wo Modell und Regeln verschieden urteilen — je entscheidendem Kriterium und je Schlüssel. Rein."""
    rr = [(b, q) for b, q in zip(beob, p) if b.urteil_regel == "relevant" and q >= schwelle]
    nr = [(b, q) for b, q in zip(beob, p) if b.urteil_regel == "nicht_relevant" and q < schwelle]
    je_k = Counter(b.kriterium or "?" for b, _ in rr)
    je_s: dict[str, dict] = {}
    for b, q in rr:
        e = je_s.setdefault(b.schluessel or "?", {"schluessel": b.schluessel, "n": 0, "ki_rauschen": 0,
                                                    "kriterium": b.kriterium, "beispiel": b.text[:160]})
        e["n"] += 1
        e["ki_rauschen"] += b.y
    return {"regel_relevant_modell_rauschen": len(rr),
            "davon_label_rauschen": sum(b.y for b, _ in rr),
            "je_kriterium": dict(je_k.most_common()),
            "schluessel": sorted(je_s.values(), key=lambda e: -e["n"])[:n],
            "regel_nicht_relevant_modell_relevant": len(nr),
            "regel_relevant_gesamt": sum(1 for b in beob if b.urteil_regel == "relevant")}


# ── Wirkung im Raster ─────────────────────────────────────────────────────────────────────────────────────
_MODELL: dict | None = None


def setze(modell: dict | None) -> None:
    global _MODELL
    _MODELL = modell


def aktuelles() -> dict | None:
    return _MODELL


@dataclass
class Signal:
    p: float
    grund: str
    modus: str


def pruefe(quelle: str, tags, felder: dict | None, kriterien, jetzt: datetime | None = None,
           modell: dict | None = None, breite: int | None = None, schluessel: str | None = None,
           auff_wert: float | None = None) -> Signal | None:
    """Signal, wenn das Modell die Gruppe für Rauschen hält (P ≥ Schwelle) — sonst None. Rein bis auf den Cache."""
    m = modell if modell is not None else _MODELL
    if not m or m.get("modus") not in ("test", "bereit") or not m.get("gewichte"):
        return None
    st = (jetzt or datetime.now(timezone.utc)).hour
    mk = merkmale(quelle, tags, (felder or {}).get("level"), kriterien, st, breite=breite) \
        + variante_merkmale(m, schluessel, st, auff_wert)
    p = wahrscheinlichkeit(m, mk)
    if p < m.get("schwelle", 1.0):
        return None
    tr = ", ".join(f"{f} ({w:+.2f})" for f, w in tragend(m, mk))
    return Signal(p, f"Modell ({m['modus']}, {m.get('variante') or 'basis'}): Rauschen P = {p:.2f} ≥ {m['schwelle']:.2f} — {tr or 'nur Grundrate'}",
                  m["modus"])


# ── Laden, Speichern, Daten ───────────────────────────────────────────────────────────────────────────────
MAPPING = {"mappings": {"dynamic": False, "properties": {"zeit": {"type": "date"}, "modus": {"type": "keyword"}}}}
QUELLFELDER = ["zeit", "quelle", "hashtags", "kriterien", "level", "ai_relevanz", "herkunft", "relevanz", "urteil",
               "schluessel", "text", "aenderungen", "host"]
#: Fenster der Breite: wie das Raster (lauf.FENSTER = 5 min)
BREITE_FENSTER_S = 300


def _fenster(zeit) -> int | None:
    try:
        return int(datetime.fromisoformat(str(zeit).replace("Z", "+00:00")).timestamp()) // BREITE_FENSTER_S
    except (TypeError, ValueError):
        return None


def breiten(docs: list[dict]) -> dict[tuple, int]:
    """(quelle, schluessel, 5-min-Fenster) → Zahl verschiedener Hosts unter den RELEVANTEN gespeicherten Nachrichten —
    dieselbe Zählung wie raster.mit_modell (dort über die relevanten Treffer eines Fensters). Rein."""
    hosts: dict[tuple, set] = {}
    for d in docs:
        if d.get("urteil") == "relevant" and d.get("schluessel"):
            hosts.setdefault((d.get("quelle"), d["schluessel"], _fenster(d.get("zeit"))), set()).add(d.get("host"))
    return {k: len(v) for k, v in hosts.items()}


def regel_urteil(regeln, doc: dict) -> int | None:
    """1, wenn die geltenden Regeln über die Eingangs-Tags der Nachricht BLOCK sagen, 0 bei FORWARD. Rein."""
    from app.services.log_watch.regelwerk import FORWARD, bewerte
    if not regeln:
        return None
    spaeter = nachtraeglich(doc.get("aenderungen"))
    tags = {t for t in doc.get("hashtags") or [] if t not in spaeter and not t.startswith(("signal:modell", "befund:",
                                                                                           "incident:"))}
    lv = doc.get("level")
    e = bewerte(regeln, tags, {"level": lv} if lv is not None else {})
    return 0 if e.aktion == FORWARD else 1


async def lade() -> dict | None:
    """Das jüngste gespeicherte Modell (oder None, benannt im Log)."""
    from app.core.opensearch import get_opensearch
    try:
        r = await get_opensearch().search(index=INDEX, ignore_unavailable=True,
                                          body={"size": 1, "sort": [{"zeit": "desc"}]})
    except Exception as e:  # noqa: BLE001
        log.warning("Relevanz-Modell nicht geladen: %s", e)
        return None
    hits = r.get("hits", {}).get("hits", [])
    return hits[0]["_source"] if hits else None


async def _alle(tage: float, seite: int = 5000, nur_ohne_mensch: bool = False) -> list[tuple[str, dict]]:
    """Alle gespeicherten Nachrichten (ohne KI-Befunde) der letzten `tage` — für Breite, Labels und Prüflisten."""
    from datetime import timedelta

    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    ohne = [{"term": {"quelle": "ki"}}] + ([{"term": {"herkunft": "mensch"}}] if nur_ohne_mensch else [])
    q = {"bool": {"filter": [{"range": {"zeit": {"gte": seit}}}], "must_not": ohne}}
    out: list[tuple[str, dict]] = []
    nach = None
    os_ = get_opensearch()
    while True:
        body = {"size": seite, "query": q, "_source": QUELLFELDER, "sort": [{"zeit": "asc"}, {"_id": "asc"}]}
        if nach:
            body["search_after"] = nach
        r = await os_.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body=body)
        hits = r["hits"]["hits"]
        out += [(h["_id"], h["_source"]) for h in hits]
        if len(hits) < seite:
            return out
        nach = hits[-1]["sort"]


async def _regeln():
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.definition import aktuelle
    async with AsyncSessionLocal() as db:
        return (await aktuelle(db)).regeln


async def _jobs() -> set[str]:
    """{"key|h"}: Muster, die in den letzten 7 Tagen an ≥ JOB_TAGE Tagen zur Stunde h auftraten und höchstens
    JOB_STUNDEN_JE_TAG_MAX Stunden je Tag aktiv sind (periodisch, nicht dauernd) — aus log_template_hourly."""
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "with h as (select key, date_trunc('day', hour_start) d, extract(hour from hour_start)::int st "
            "from log_template_hourly where hour_start >= now() - interval '7 days' and count > 0), "
            "k as (select key, count(*)::float / nullif(count(distinct d), 0) je_tag from h group by key) "
            "select h.key, h.st from h join k using (key) where k.je_tag <= :maxh "
            "group by h.key, h.st having count(distinct h.d) >= :tage"),
            {"maxh": JOB_STUNDEN_JE_TAG_MAX, "tage": JOB_TAGE})).all()
    return {f"{k}|{h}" for k, h in rows}


async def _auff_werte(docs: list[dict]) -> tuple[dict[tuple[str, int], float], dict | None]:
    """IsolationForest-Wert je (Muster, 5-min-Bucket) der Graylog-Nachrichten — mit dem Modell im Speicher
    (auffaellig.aktuelles; im Backend-Prozess nach dem ersten Lauf). Ohne Modell: leer, benannt im Ergebnis."""
    from datetime import timedelta as _td

    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.services.log_watch import auffaellig
    st = auffaellig.aktuelles()
    if not st:
        return {}, None
    paare = set()
    for d in docs:
        k = str(d.get("schluessel") or "")
        f = _fenster(d.get("zeit"))
        if d.get("quelle") == "graylog" and k.startswith("muster:") and f is not None:
            paare.add((k[7:], f))
    if not paare:
        return {}, None
    keys, starts = zip(*paare)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "select b.key, b.bucket_start, b.count, b.n_entities from log_template_buckets b "
            "join unnest(cast(:k as text[]), cast(:t as timestamptz[])) as p(key, bs) "
            "on b.key = p.key and b.bucket_start = p.bs"),
            {"k": list(keys), "t": [datetime.fromtimestamp(f * BREITE_FENSTER_S, timezone.utc) for f in starts]})).all()
    if not rows:
        return {}, None
    xs, ids = [], []
    for key, b, n, h in rows:
        mittel, praesenz = st["stats"].get(key, (0.0, 0.0))
        erst = st["erst"].get(key)
        alter = (b - erst).total_seconds() / 3600 if erst else 0.0
        xs.append(auffaellig.merkmal_zeile(int(n), mittel, int(h or 0), alter, praesenz))
        ids.append((key, int(b.timestamp()) // BREITE_FENSTER_S))
    werte = [-float(v) for v in st["modell"].score_samples(xs)]
    return dict(zip(ids, werte)), {"hoch": st["schwelle"], "erhoeht": st.get("q90", st["schwelle"])}


async def beobachtungen_mit_kontext(tage: float = 30) -> tuple[list[Beobachtung], dict]:
    """Beobachtungen und der Kontext der Herausforderer (Job-Fenster, Auffälligkeits-Grenzen)."""
    docs = await _alle(tage)
    kontext: dict = {}
    try:
        kontext["jobs"] = sorted(await _jobs())
    except Exception as e:  # noqa: BLE001 — Variante fällt dann aus, benannt
        kontext["jobs_fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
    try:
        auff, grenzen = await _auff_werte([d for _, d in docs])
        kontext["auff_grenzen"] = grenzen
    except Exception as e:  # noqa: BLE001
        auff, kontext["auff_fehler"] = {}, f"{type(e).__name__}: {str(e)[:200]}"
    beob = await _beobachtungen_aus(docs)
    jobs = set(kontext.get("jobs") or [])
    for b in beob:
        st = _stunde(b.zeit)
        extra = {"job_fenster": job_merkmal(jobs, b.schluessel, st)}
        f = _fenster(b.zeit)
        if b.schluessel.startswith("muster:") and f is not None:
            extra["auffaellig"] = auff_stufe(auff.get((b.schluessel[7:], f)), kontext.get("auff_grenzen"))
        b.extra = extra
    return beob, kontext


async def beobachtungen(tage: float = 30) -> list[Beobachtung]:
    return (await beobachtungen_mit_kontext(tage))[0]


async def _beobachtungen_aus(docs: list[tuple[str, dict]]) -> list[Beobachtung]:
    """Alle Nachrichten mit unabhängigem Urteil (Mensch oder KI), mit Breite der Episode und dem Urteil der geltenden
    Regeln."""
    br = breiten([d for _, d in docs])
    crit = crit_zeiten([d for _, d in docs])
    regeln = await _regeln()
    out: list[Beobachtung] = []
    for i, d in docs:
        if label(d) is None:
            continue
        b = beobachtung(i, d, br.get((d.get("quelle"), d.get("schluessel"), _fenster(d.get("zeit")))),
                        regel_urteil(regeln, d) if d.get("herkunft") == "mensch" else None)
        if b:
            b.folge = folge(crit, d)
            out.append(b)
    return out


def _ts(zeit) -> float | None:
    try:
        return datetime.fromisoformat(str(zeit).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def crit_zeiten(docs: list[dict]) -> dict[str, list[float]]:
    """Host → sortierte Zeiten gespeicherter CheckMK-Nachrichten mit zustand:crit. Rein."""
    out: dict[str, list[float]] = {}
    for d in docs:
        if d.get("quelle") == "checkmk" and "zustand:crit" in (d.get("hashtags") or []) and d.get("host"):
            t = _ts(d.get("zeit"))
            if t is not None:
                out.setdefault(d["host"], []).append(t)
    for v in out.values():
        v.sort()
    return out


def folge(crit: dict[str, list[float]], doc: dict) -> int | None:
    """1, wenn binnen FOLGE_FENSTER nach der Nachricht ein CheckMK-CRIT am selben Host gespeichert wurde, sonst 0;
    None für CheckMK-Nachrichten selbst (sie wären ihre eigene Folge) und ohne Host/Zeit. Rein."""
    import bisect
    if doc.get("quelle") == "checkmk" or not doc.get("host"):
        return None
    t = _ts(doc.get("zeit"))
    if t is None:
        return None
    z = crit.get(doc["host"], [])
    i = bisect.bisect_right(z, t)
    return int(i < len(z) and z[i] <= t + FOLGE_FENSTER_S)


def pruefliste(modell: dict, docs: list[tuple[str, dict]], br: dict[tuple, int], art_: str, limit: int = 20) -> dict:
    """Gold-Labels gezielt holen (Rat der vi-mcp-Sitzung): `widerspruch` = Regel und Modell urteilen verschieden,
    stärkster Widerspruch zuerst; `unsicher` = P nahe 0,5. Ein Fall je Schlüssel — jeder Klick soll eine unabhängige
    Entscheidung sein. Ohne Fälle mit Menschen-Urteil. Rein."""
    if art_ not in ("widerspruch", "unsicher"):
        raise ValueError("art: widerspruch | unsicher")
    s = modell.get("schwelle", 1.0)
    besten: dict[str, dict] = {}
    for i, d in docs:
        if d.get("herkunft") == "mensch":
            continue
        mk = merkmale(d.get("quelle") or "", d.get("hashtags") or [], d.get("level"), d.get("kriterien") or [],
                      _stunde(d.get("zeit")), nachtraeglich=nachtraeglich(d.get("aenderungen")),
                      breite=br.get((d.get("quelle"), d.get("schluessel"), _fenster(d.get("zeit"))))) \
            + variante_merkmale(modell, d.get("schluessel"), _stunde(d.get("zeit")), None)
        p = wahrscheinlichkeit(modell, mk)
        regel_rel = d.get("urteil") == "relevant"
        if art_ == "widerspruch":
            if regel_rel == (p < s):
                continue
            rang = abs(p - s)
        else:
            rang = -abs(p - 0.5)
        k = d.get("schluessel") or i
        if k not in besten or rang > besten[k]["_rang"]:
            besten[k] = {"_rang": rang, "id": i, "zeit": d.get("zeit"), "quelle": d.get("quelle"),
                         "host": d.get("host"), "schluessel": d.get("schluessel"), "text": str(d.get("text") or "")[:300],
                         "regel": d.get("urteil"), "kriterien": d.get("kriterien"), "ki_relevanz": d.get("ai_relevanz"),
                         "p_rauschen": round(p, 4), "tragend": tragend(modell, mk)}
    faelle = sorted(besten.values(), key=lambda f: -f["_rang"])
    for f in faelle:
        f.pop("_rang")
    return {"art": art_, "schwelle": s, "faelle_gesamt": len(faelle), "faelle": faelle[:limit],
            "urteilen": "POST /log-watch/nachrichten/{id}/urteil — relevant (R…) oder nicht_relevant (N4/N1), "
                        "Herkunft mensch"}


async def lade_pruefliste(art_: str, tage: float = 7, limit: int = 20) -> dict:
    m = await lade()
    if not m or not m.get("gewichte"):
        return {"art": art_, "faelle_gesamt": 0, "faelle": [], "grund": zusammenfassung(m).get("grund")}
    docs = await _alle(tage, nur_ohne_mensch=True)
    return pruefliste(m, docs, breiten([d for _, d in docs]), art_, limit)


async def trainiere_und_speichere(tage: float = 30) -> dict:
    """Nachtlauf: Daten holen, trainieren, speichern, in den Raster-Cache setzen. Ergebnis ohne Gewichte."""
    from app.core.opensearch import get_opensearch
    beob, kontext = await beobachtungen_mit_kontext(tage)
    m = trainiere(beob)
    m["daten"] = {"tage": tage, "beobachtungen": len(beob), "index": "cs-logs-*",
                  **{k: v for k, v in kontext.items() if k.endswith("_fehler")},
                  "jobs": len(kontext.get("jobs") or []), "auff_grenzen": kontext.get("auff_grenzen")}
    # nur, was die gewählte Variante zum Bewerten braucht
    if m.get("variante") == "job_fenster":
        m["jobs"] = kontext.get("jobs") or []
    if m.get("variante") == "auffaellig":
        m["auff_grenzen"] = kontext.get("auff_grenzen")
    os_ = get_opensearch()
    if not await os_.indices.exists(index=INDEX):
        await os_.indices.create(index=INDEX, body=MAPPING)
    await os_.index(index=INDEX, id=m["zeit"], body=m, refresh=True)
    setze(m)
    log.info("Relevanz-Modell: %s — %s", m["modus"], m["grund"])
    return zusammenfassung(m)


def zusammenfassung(m: dict | None) -> dict:
    """Modell ohne die vollständige Gewichtsliste — für API und MCP. Rein."""
    if not m:
        return {"modus": "zu_wenig_daten", "grund": "noch kein Modell trainiert (stündlich zur Minute 35 oder "
                                                    "POST /log-watch/modell/trainieren)"}
    return {k: v for k, v in m.items() if k != "gewichte"} | {"gewichte_n": len(m.get("gewichte") or {})}
