"""Stufe 0 der Firewall: Graylogs eigene Pipeline-Regeln, die Nachrichten verwerfen.

Was dort verworfen wird, erreicht das Raster nie — darum wird es sichtbar gemacht
(Quelltext, Pipeline, Stufe, Streams) und gezählt („an der Quelle verworfen“), statt still
zu fehlen. Nur lesend: Änderungen an Graylog sind Produktion und brauchen Freigabe.

Gemessen 06.11.: drei Verwerf-Regeln in `[proc]routing` (drop_http_success ≈ 880 000/h,
drop_container_noise ≈ 145 000/h, drop_cue_zipline_xml ≈ 2 200/h). Graylog zählt je Knoten;
der Load-Balancer verteilt Anfragen zufällig — gefragt wird, bis jeder Knoten geantwortet
hat. Fehlt ein Knoten, ist die Summe als unvollständig benannt.
"""
from __future__ import annotations

import re

METRIK = "org.graylog.plugins.pipelineprocessor.ast.Rule.{id}.matched"
#: höchstens so viele Anfragen je Regel, um alle Knoten zu erreichen
VERSUCHE = 15
_FELD = re.compile(r'has_field\("([^"]+)"\)|\$message\.(\w+)')


def verwerf_regeln(pipelines: list[dict], regeln: list[dict], verbindungen: list[dict],
                   streams: dict[str, str]) -> list[dict]:
    """Regeln mit drop_message(), mit Pipeline, Stufe, Streams und geprüften Feldern."""
    out = []
    for r in regeln:
        quelle = r.get("source") or ""
        if "drop_message(" not in quelle:
            continue
        orte = []
        for p in pipelines:
            for st in p.get("stages") or []:
                if r.get("title") in (st.get("rules") or []):
                    ziel = [streams.get(v["stream_id"], v["stream_id"]) for v in verbindungen
                            if p["id"] in (v.get("pipeline_ids") or [])]
                    orte.append({"pipeline": p.get("title"), "stufe": st.get("stage"), "streams": ziel})
        felder = sorted({a or b for a, b in _FELD.findall(quelle)} - {"message", "_msg_lc"} | (
            {"message"} if "$message.message" in quelle else set()))
        out.append({"id": r.get("id"), "titel": r.get("title"), "quelltext": quelle,
                    "geaendert": r.get("modified_at"), "orte": orte, "felder": felder,
                    "aktiv": bool(orte)})
    return out


def summe(messungen: list[tuple[str, float]], knoten: set[str]) -> dict:
    """Je Knoten (zuletzt) gemessene 15-min-Rate (1/s) → Summe je Stunde und Vollständigkeit."""
    je: dict[str, float] = {}
    for k, rate in messungen:
        je[k] = rate
    gesehen = set(je) & knoten if knoten else set(je)
    return {"pro_stunde": round(sum(je[k] for k in gesehen) * 3600),
            "knoten_gesehen": len(gesehen), "knoten_erwartet": len(knoten) or len(je),
            "vollstaendig": bool(knoten) and gesehen == knoten}


async def lies(gl) -> tuple[list[dict], set[str]]:
    """(Verwerf-Regeln, erwartete Knoten-IDs) aus der Graylog-API."""
    async with gl._client(timeout=60.0) as c:
        h = gl._headers()
        pipelines = (await c.get(f"{gl.base_url}/api/system/pipelines/pipeline", headers=h)).json()
        regeln = (await c.get(f"{gl.base_url}/api/system/pipelines/rule", headers=h)).json()
        verbindungen = (await c.get(f"{gl.base_url}/api/system/pipelines/connections", headers=h)).json()
        streams = {s["id"]: s["title"] for s in
                   (await c.get(f"{gl.base_url}/api/streams", headers=h)).json().get("streams", [])}
        knoten = {n["node_id"] for n in
                  (await c.get(f"{gl.base_url}/api/system/cluster/nodes", headers=h)).json().get("nodes", [])}
    return verwerf_regeln(pipelines, regeln, verbindungen, streams), knoten


async def zaehle(gl, regeln: list[dict], knoten: set[str]) -> dict[str, dict]:
    """Je Regel die Summe über alle Knoten (15-min-Rate × 3600)."""
    out = {}
    async with gl._client(timeout=30.0) as c:
        h = gl._headers()
        for r in regeln:
            messungen: list[tuple[str, float]] = []
            for _ in range(VERSUCHE):
                node = (await c.get(f"{gl.base_url}/api/system", headers=h)).json().get("node_id", "?")
                m = await c.get(f"{gl.base_url}/api/system/metrics/" + METRIK.format(id=r["id"]), headers=h)
                if m.status_code == 200:
                    messungen.append((node, float(m.json().get("m15_rate") or 0.0)))
                if knoten and {k for k, _ in messungen} >= knoten:
                    break
            out[r["titel"]] = summe(messungen, knoten)
    return out


# ── Widerspruchsprüfung: Themen, die Stufe 0 bei Container-Logs verwirft ───────

_BEHALTEN_WORT = re.compile(r'contains\(to_string\(\$message\._msg_lc\),\s*"([^"]+)"\)')
#: regex("…", to_string($message._msg_lc)) im Behalten-Block (seit 07.11. 13:54 UTC: unsere Themen-Ausdrücke)
_BEHALTEN_REGEX = re.compile(r'regex\("((?:[^"\\]|\\.)*)",\s*to_string\(\$message\._msg_lc\)\)')


def _literal(s: str) -> str:
    """Graylog-Zeichenkettenliteral → Wert (\\\\ → \\, \\" → ")."""
    return re.sub(r"\\(.)", r"\1", s)


def behalte_ausdruecke(quelltext: str) -> list[str]:
    """Die regex()-Muster über _msg_lc aus einem Regeltext, als Wert (nicht als Literal)."""
    return [_literal(m) for m in _BEHALTEN_REGEX.findall(quelltext)]


#: Themen, die eine Verwerf-Regel über ein FELD behält, nicht über ein Wort: (Muster im Regeltext, Thema)
_BEHALTEN_FELD = ((re.compile(r"http_response_code\)\s*>=\s*400"), "http-5xx"),)


def widersprueche(regeln: list[dict], themen) -> list[dict]:
    """Themen, deren typische Zeile eine Verwerf-Regel für Container-Logs entfernt.

    Eine Regel der Form „Container-Log ohne eines dieser Wörter → verwerfen“ (Behalte-Liste aus
    dem Regeltext) entfernt jede Zeile eines Themas, die keines der Wörter enthält: das Raster
    sieht dieses Thema aus Container-Logs dann nur, wenn die Zeile zufällig ein Fehlerwort trägt.
    Ein regex()-Zweig über _msg_lc im Behalten-Block zählt ebenfalls als „behält“.
    """
    out = []
    for r in regeln:
        q = r.get("quelltext") or ""
        if 'has_field("container_name")' not in q or "NOT (" not in q:
            continue
        woerter = [w.lower() for w in _BEHALTEN_WORT.findall(q)]
        ausdruecke = []
        for a in behalte_ausdruecke(q):
            try:
                ausdruecke.append(re.compile(a))
            except re.error:      # Java-Eigenheit, die Python nicht kennt: Zweig zählt nicht als „behält“
                pass
        if not woerter and not ausdruecke:
            continue
        ueber_feld = {t for muster, t in _BEHALTEN_FELD if muster.search(q)}
        for t in themen:
            if not t.beispiel or t.id in ueber_feld:
                continue
            zeile = t.beispiel.lower()                       # _msg_lc ist die kleingeschriebene Zeile
            if not any(w in zeile for w in woerter) and not any(rx.search(zeile) for rx in ausdruecke):
                out.append({"regel": r["titel"], "thema": t.id, "thema_text": t.text,
                            "beispiel": t.beispiel, "behaelt_nur_mit": woerter})
    return out


# ── Graylog-Regeltext aus unserem Vokabular (eine Quelle für Graylog und Raster) ──

def themen_ausdruck_stand(regeln: list[dict], themen) -> list[dict]:
    """Je Verwerf-Regel mit Themen-Ausdruck: entspricht er noch themen_regex(themen)? Der Ausdruck
    wird von Hand in Graylog eingespielt — ändert sich das Vokabular, veraltet er still."""
    soll = themen_regex(themen)
    return [{"regel": r["titel"], "aktuell": soll in behalte_ausdruecke(r.get("quelltext") or "")}
            for r in regeln if any(a.startswith("(?is).*(?:") for a in behalte_ausdruecke(r.get("quelltext") or ""))]


def themen_regex(themen) -> str:
    """Ein Java-kompatibler Ausdruck aus den Mustern der Themen (für Graylogs regex()).

    `(?is).*(?:…).*` passt für find() wie für matches(); die Muster nutzen nur \\b, \\S, \\d,
    Gruppen und Wiederholungen — in Java gleichbedeutend.
    """
    return "(?is).*(?:" + "|".join(f"(?:{t.muster.pattern})" for t in themen) + ").*"


def zaehlregel(verwerf_quelltext: str, themen, name: str = "count_topic_would_drop") -> str:
    """Zählregel „durch Themen gerettet“ (Bedeutung seit 07.11. 13:54 UTC, Name in Graylog unverändert):
    Bedingung = Verwerf-Bedingung OHNE den Themen-Zweig UND Themen-Ausdruck — also die Zeilen, die
    allein der Themen-Ausdruck vor dem Verwerfen bewahrt. Setzt kein Feld (die Zeilen bleiben ja
    erhalten); gemessen wird nur ihr Trefferzähler."""
    m = re.search(r"\bwhen\b(.*)\bthen\b", verwerf_quelltext, re.S)
    if not m:
        raise ValueError("Verwerf-Regel ohne when … then")
    bedingung = re.sub(r"\s*OR\s+" + _BEHALTEN_REGEX.pattern + r"\.matches\s*==\s*true", "",
                       m.group(1)).strip()
    muster = themen_regex(themen).replace("\\", "\\\\").replace('"', '\\"')
    return (f'rule "{name}"\nwhen\n  ({bedingung})\n'
            f'  AND regex("{muster}", to_string($message._msg_lc)).matches == true\n'
            f'then\nend')
