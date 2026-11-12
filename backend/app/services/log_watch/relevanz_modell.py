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
Schwelle erreicht. Entscheiden tut weiter allein das Regelwerk: erst eine Regel auf dieses Signal (N1-MOD,
Standard im Testmodus) wirkt — und eine aktive solche Regel greift nur bei Modus „bereit“ (raster._urteil).

Modus, erschöpfend, immer mit Grund: zu_wenig_daten · test (urteilt mit, wirkt nicht) · bereit (eine aktive Regel
darf es benutzen). „bereit“ verlangt Gold-Labels, Holdout-Kennzahlen und ein Gewichts-Audit ohne Befund — feste
Regeln, kein LLM (ein LLM, das Gewichte prüft, liest auch Logtexte und kann von ihnen gesteuert werden).
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
#: Von dem, was relevant ist, darf das Modell höchstens 1 % als Rauschen einstufen (Schwelle danach gewählt)
RECALL_RELEVANT_MIN = 0.99
L2 = 1e-3
#: Tags, die nach dem Urteil entstehen oder das Label tragen — nie Merkmal
OHNE = ("befund:", "incident:", "signal:modell")
TOP_N = 10


# ── Merkmale ──────────────────────────────────────────────────────────────────────────────────────────────
def merkmale(quelle: str, tags, level=None, kriterien=None, stunde: int | None = None) -> list[str]:
    """Merkmale einer Nachrichtengruppe — dieselbe Funktion im Training und im Raster. Rein."""
    m = {f"quelle:{quelle}"} if quelle else set()
    m |= {t for t in tags or [] if ":" in t and not t.startswith(OHNE)}
    try:
        if level is not None and str(level) != "":
            m.add(f"level:{int(float(level))}")
    except (TypeError, ValueError):
        pass
    # R7 setzt die Historie erst nachträglich (markiere_r7) — im Raster unbekannt, darum nie Merkmal
    m |= {f"kriterium:{k}" for k in kriterien or [] if k != "R7"}
    if stunde is not None:
        m.add(f"stunde:{int(stunde):02d}")
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


def beobachtung(id_: str, doc: dict) -> Beobachtung | None:
    """Ein gespeichertes Dokument aus cs-logs-* als Trainingsbeispiel — oder None (KI-Befund, ohne Label). Rein."""
    if doc.get("quelle") == "ki":
        return None
    lb = label(doc)
    if lb is None:
        return None
    y, herkunft = lb
    krit = list(doc.get("kriterien") or [])
    # Bei einem Menschen-Urteil stehen dort SEINE Kriterien, nicht die der Regel → nicht als Merkmal (sonst Leck)
    m = merkmale(doc.get("quelle") or "", doc.get("hashtags") or [], doc.get("level"),
                 [] if herkunft == "gold" else krit, _stunde(doc.get("zeit")))
    return Beobachtung(id_, str(doc.get("zeit") or ""), y, herkunft, m, str(doc.get("schluessel") or ""),
                       str(doc.get("urteil") or ""), krit[0] if krit else "", str(doc.get("text") or "")[:200])


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


def wahrscheinlichkeit(modell: dict, m: list[str]) -> float:
    g = modell.get("gewichte") or {}
    return _sig(modell.get("bias", 0.0) + sum(g.get(x, 0.0) for x in m))


def tragend(modell: dict, m: list[str], n: int = 3) -> list[tuple[str, float]]:
    """Die Gewichte, die das Urteil tragen — größter Beitrag Richtung Rauschen zuerst. Rein."""
    g = modell.get("gewichte") or {}
    return sorted(((x, g[x]) for x in m if x in g), key=lambda t: -t[1])[:n]


# ── Training ──────────────────────────────────────────────────────────────────────────────────────────────
def _fit(zeilen: list[list[int]], y: list[int], gew: list[float], nf: int, schritte: int = 600):
    """Logistische Regression mit L2, volle Gradienten mit Adam — dünn besetzte Binärmerkmale. numpy."""
    import numpy as np
    rows = np.fromiter((i for i, r in enumerate(zeilen) for _ in r), dtype=np.int64)
    cols = np.fromiter((c for r in zeilen for c in r), dtype=np.int64)
    yv, sw = np.asarray(y, dtype=float), np.asarray(gew, dtype=float)
    n = len(zeilen)
    w, b = np.zeros(nf), 0.0
    basis = float((yv * sw).sum() / sw.sum())
    b = math.log(max(basis, 1e-6) / max(1 - basis, 1e-6))
    m_w, v_w, m_b, v_b = np.zeros(nf), np.zeros(nf), 0.0, 0.0
    lr, b1, b2, eps = 0.05, 0.9, 0.999, 1e-8
    for t in range(1, schritte + 1):
        z = np.bincount(rows, weights=w[cols], minlength=n) + b
        p = 1 / (1 + np.exp(-np.clip(z, -35, 35)))
        r = (p - yv) * sw / sw.sum()
        g_w = np.bincount(cols, weights=r[rows], minlength=nf) + L2 * w
        g_b = float(r.sum())
        m_w = b1 * m_w + (1 - b1) * g_w
        v_w = b2 * v_w + (1 - b2) * g_w ** 2
        m_b = b1 * m_b + (1 - b1) * g_b
        v_b = b2 * v_b + (1 - b2) * g_b ** 2
        w -= lr * (m_w / (1 - b1 ** t)) / (np.sqrt(v_w / (1 - b2 ** t)) + eps)
        b -= lr * (m_b / (1 - b1 ** t)) / (math.sqrt(v_b / (1 - b2 ** t)) + eps)
    return [float(x) for x in w], float(b)


def teile_zeitlich(beob: list[Beobachtung]) -> tuple[list[Beobachtung], list[Beobachtung]]:
    """Training = älterer Teil, Holdout = jüngster Teil; wächst bis HOLDOUT_MAX, bis je Klasse genug drin ist. Rein."""
    s = sorted(beob, key=lambda b: b.zeit)
    anteil = HOLDOUT_ANTEIL
    while True:
        k = int(len(s) * (1 - anteil))
        ho = s[k:]
        n1 = sum(b.y for b in ho)
        if (n1 >= MIN_HOLDOUT_JE_KLASSE and len(ho) - n1 >= MIN_HOLDOUT_JE_KLASSE) or anteil >= HOLDOUT_MAX:
            return s[:k], ho
        anteil = min(HOLDOUT_MAX, anteil + 0.05)


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
    """(modus, grund) — erschöpfend; der erste fehlende Punkt ist der Grund. Rein."""
    gesamt = labels.get("gesamt") or {}
    if min(gesamt.get("rauschen", 0), gesamt.get("relevant", 0)) < MIN_JE_KLASSE:
        return "zu_wenig_daten", (f"Labels je Klasse: rauschen {gesamt.get('rauschen', 0)}, relevant "
                                  f"{gesamt.get('relevant', 0)} — nötig je {MIN_JE_KLASSE}")
    gold = labels.get("gold") or {}
    if min(gold.get("rauschen", 0), gold.get("relevant", 0)) < MIN_GOLD_JE_KLASSE:
        return "test", (f"nur KI-Urteile (Silber) reichen nicht: Mensch-Urteile (Gold) je Klasse rauschen "
                        f"{gold.get('rauschen', 0)}, relevant {gold.get('relevant', 0)} — nötig je {MIN_GOLD_JE_KLASSE}")
    a = kennzahlen.get("auc_gold")
    if a is None or a < AUC_MIN:
        return "test", f"Holdout-AUC gegen Gold {a if a is None else round(a, 3)} < {AUC_MIN}"
    r = kennzahlen.get("recall_relevant_gold")
    if r is None or r < RECALL_RELEVANT_MIN:
        return "test", f"Recall relevant (Gold, Holdout) {r if r is None else round(r, 3)} < {RECALL_RELEVANT_MIN}"
    if befunde:
        return "test", "Gewichts-Audit: " + "; ".join(f"{b['klasse']} {b['merkmal']}" for b in befunde[:4])
    return "bereit", (f"AUC {a:.3f}, Recall relevant {r:.3f} gegen Gold; Audit ohne Befund — eine aktive Regel auf "
                      f"{SIGNAL} darf wirken")


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
    train, ho = teile_zeitlich(beob)
    zaehl = Counter(x for b in train for x in set(b.merkmale))
    vok = sorted(f for f, n in zaehl.items() if n >= MIN_VORKOMMEN)
    idx = {f: i for i, f in enumerate(vok)}
    zeilen = [[idx[x] for x in b.merkmale if x in idx] for b in train]
    gew = [1.0 if b.herkunft == "gold" else SILBER_GEWICHT for b in train]
    w, bias = _fit(zeilen, [b.y for b in train], gew, len(vok))
    modell = {"gewichte": {f: round(w[i], 4) for f, i in idx.items() if abs(w[i]) >= 1e-3}, "bias": round(bias, 4)}
    p_train = [wahrscheinlichkeit(modell, b.merkmale) for b in train]
    schwelle = schwelle_fuer([p for p, b in zip(p_train, train) if b.y == 0])
    p_ho = [wahrscheinlichkeit(modell, b.merkmale) for b in ho]
    train_schl = {b.schluessel for b in train}

    def kennz(auswahl):
        sel = [(p, b) for p, b in zip(p_ho, ho) if auswahl(b)]
        if not sel:
            return None, None, None, 0
        ps, ys = [p for p, _ in sel], [b.y for _, b in sel]
        rel = [p for p, y in zip(ps, ys) if y == 0]
        rau = [p for p, y in zip(ps, ys) if y == 1]
        return (auc(ps, ys), (sum(1 for p in rel if p < schwelle) / len(rel)) if rel else None,
                (sum(1 for p in rau if p >= schwelle) / len(rau)) if rau else None, len(sel))

    a, rr, re_, n = kennz(lambda b: True)
    a_neu, rr_neu, re_neu, n_neu = kennz(lambda b: b.schluessel not in train_schl)
    a_g, rr_g, re_g, n_g = kennz(lambda b: b.herkunft == "gold")
    kz = {"auc": a, "recall_relevant": rr, "rauschen_erkannt": re_, "holdout_n": n,
          "holdout_von": ho[0].zeit if ho else None, "holdout_bis": ho[-1].zeit if ho else None,
          "train_n": len(train), "merkmale": len(vok), "merkmale_verworfen": len(zaehl) - len(vok),
          # Schlüssel, die im Training nie vorkamen: misst Verallgemeinerung statt Wiedererkennen
          "auc_unbekannte_schluessel": a_neu, "recall_relevant_unbekannt": rr_neu,
          "rauschen_erkannt_unbekannt": re_neu, "holdout_unbekannt_n": n_neu,
          "auc_gold": a_g, "recall_relevant_gold": rr_g, "rauschen_erkannt_gold": re_g, "holdout_gold_n": n_g,
          "basisrate_rauschen": round(sum(b.y for b in train) / len(train), 4)}
    kz = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in kz.items()}
    befunde = audit(modell["gewichte"], train,
                    [b.merkmale for p, b in zip(p_ho, ho) if p >= schwelle])
    m, grund = modus(labels, kz, befunde)
    alle_p = p_train + p_ho
    return {"zeit": jetzt.isoformat(), "modus": m, "grund": grund, "labels": labels, **modell,
            "schwelle": round(schwelle, 4), "kennzahlen": kz, "audit": befunde,
            "staerkste": [{"merkmal": f, "gewicht": g} for f, g in
                          sorted(modell["gewichte"].items(), key=lambda t: -abs(t[1]))[:25]],
            "widersprueche": widersprueche(train + ho, alle_p, schwelle)}


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
    freigegeben: bool
    modus: str


def pruefe(quelle: str, tags, felder: dict | None, kriterien, jetzt: datetime | None = None,
           modell: dict | None = None) -> Signal | None:
    """Signal, wenn das Modell die Gruppe für Rauschen hält (P ≥ Schwelle) — sonst None. Rein bis auf den Cache."""
    m = modell if modell is not None else _MODELL
    if not m or m.get("modus") not in ("test", "bereit") or not m.get("gewichte"):
        return None
    st = (jetzt or datetime.now(timezone.utc)).hour
    mk = merkmale(quelle, tags, (felder or {}).get("level"), kriterien, st)
    p = wahrscheinlichkeit(m, mk)
    if p < m.get("schwelle", 1.0):
        return None
    tr = ", ".join(f"{f} ({w:+.2f})" for f, w in tragend(m, mk))
    return Signal(p, f"Modell ({m['modus']}): Rauschen P = {p:.2f} ≥ {m['schwelle']:.2f} — {tr or 'nur Grundrate'}",
                  m["modus"] == "bereit", m["modus"])


# ── Laden, Speichern, Daten ───────────────────────────────────────────────────────────────────────────────
MAPPING = {"mappings": {"dynamic": False, "properties": {"zeit": {"type": "date"}, "modus": {"type": "keyword"}}}}
QUELLFELDER = ["zeit", "quelle", "hashtags", "kriterien", "level", "ai_relevanz", "herkunft", "relevanz", "urteil",
               "schluessel", "text"]


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


async def beobachtungen(tage: float = 30, seite: int = 5000) -> list[Beobachtung]:
    """Alle Nachrichten mit unabhängigem Urteil (Mensch oder KI) aus cs-logs-* der letzten `tage`."""
    from datetime import timedelta

    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    q = {"bool": {"filter": [{"range": {"zeit": {"gte": seit}}}],
                  "must_not": [{"term": {"quelle": "ki"}}],
                  "should": [{"exists": {"field": "ai_relevanz"}}, {"term": {"herkunft": "mensch"}}],
                  "minimum_should_match": 1}}
    out: list[Beobachtung] = []
    nach = None
    os_ = get_opensearch()
    while True:
        body = {"size": seite, "query": q, "_source": QUELLFELDER, "sort": [{"zeit": "asc"}, {"_id": "asc"}]}
        if nach:
            body["search_after"] = nach
        r = await os_.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body=body)
        hits = r["hits"]["hits"]
        for h in hits:
            b = beobachtung(h["_id"], h["_source"])
            if b:
                out.append(b)
        if len(hits) < seite:
            return out
        nach = hits[-1]["sort"]


async def trainiere_und_speichere(tage: float = 30) -> dict:
    """Nachtlauf: Daten holen, trainieren, speichern, in den Raster-Cache setzen. Ergebnis ohne Gewichte."""
    from app.core.opensearch import get_opensearch
    beob = await beobachtungen(tage)
    m = trainiere(beob)
    m["daten"] = {"tage": tage, "beobachtungen": len(beob), "index": "cs-logs-*"}
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
        return {"modus": "zu_wenig_daten", "grund": "noch kein Modell trainiert (Nachtlauf 03:35 UTC oder "
                                                    "POST /log-watch/modell/trainieren)"}
    return {k: v for k, v in m.items() if k != "gewichte"} | {"gewichte_n": len(m.get("gewichte") or {})}
