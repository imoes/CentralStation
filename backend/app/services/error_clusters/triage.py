"""KI-Einschätzung eines Fehler-Clusters — nur Text, entscheidet nichts.

Ob gemeldet wird, haben die Regeln schon entschieden. Das LLM bekommt den aufbereiteten
Cluster (Muster, Beispiele, Verlauf gegen Grundlast, Container, gleichzeitig auffällige
Dienste, CheckMK-Alerts der betroffenen Hosts) und schreibt Zusammenfassung, vermutete
Ursache und lesende Prüfschritte. Kein Token-Limit (globale Regel); kein Budget — gegen
eine Flut schützt die Episodenlogik (ein Aufruf je Episode, einer je Verdopplung).
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

SYSTEM = (
    "Du bist ein erfahrener Site-Reliability-Engineer. Du bekommst einen automatisch "
    "erkannten Fehler-Cluster aus Graylog: ein Log-Muster, das plötzlich auf vielen "
    "Containern oder weit über seiner Grundlast auftritt, dazu gleichzeitig auffällige "
    "Dienste und CheckMK-Alerts der betroffenen Hosts.\n"
    "Schätze ein, was passiert ist und womit man anfängt. Halte dich an die Daten; "
    "Vermutungen kennzeichnest du als solche. Nenne nur LESENDE Prüfschritte (Logs, "
    "Status, Konfiguration ansehen) — keine Änderungen, keine Neustarts.\n"
    "Antworte ausschließlich mit einem JSON-Objekt, ohne Text davor oder danach:\n"
    '{"titel": str (≤ 80 Zeichen), "zusammenfassung": str (2–3 Sätze), '
    '"vermutete_ursache": str, "betroffene_komponente": str, '
    '"schweregrad": "kritisch"|"hoch"|"mittel", "konfidenz": "hoch"|"mittel"|"niedrig", '
    '"verwandte_signale": [str], "pruefschritte": [str]}'
)

KEYS = ("titel", "zusammenfassung", "vermutete_ursache", "betroffene_komponente",
        "schweregrad", "konfidenz", "verwandte_signale", "pruefschritte")


def build_prompt(ctx: dict, correlated: list[dict], checkmk: list[dict]) -> str:
    """Kompakte, deterministische Eingabe — nur was zur Einschätzung gebraucht wird."""
    payload = {
        "muster": ctx.get("template"),
        "beispiele": (ctx.get("examples") or [])[:5],
        "regeln": ctx.get("rules"),
        "begruendung": ctx.get("reasons"),
        "zeilen_15min": ctx.get("count15"),
        "erwartet_15min": ctx.get("expected15"),
        "zeilen_gesamt": ctx.get("total"),
        "seit": ctx.get("started_at"),
        "container": [c["name"] for c in (ctx.get("containers") or [])[:15]],
        "anzahl_container": len(ctx.get("containers") or []),
        "hosts": ctx.get("hosts"),
        "gleichzeitig_auffaellig": correlated[:10],
        "checkmk_alerts_betroffene_hosts": checkmk[:15],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def parse(raw: str) -> dict | None:
    """JSON aus der Antwort holen; Denkblöcke und Code-Zäune werden ignoriert."""
    if not raw:
        return None
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("zusammenfassung"):
        return None
    out = {k: data.get(k) for k in KEYS}
    for k in ("verwandte_signale", "pruefschritte"):
        v = out.get(k)
        out[k] = [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
    return out


async def triage(db, ctx: dict, correlated: list[dict], checkmk: list[dict]) -> dict | None:
    from app.services.llm_client import generate_text
    from app.services.settings import get_active_llm_config

    cfg = await get_active_llm_config(db)
    if not cfg.is_configured:
        return None
    raw = await generate_text(
        cfg,
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": build_prompt(ctx, correlated, checkmk)}],
        temperature=0.1,
        reasoning_effort="low",
    )
    result = parse(raw)
    if result is None:
        log.warning("error_clusters triage: unparseable answer (%d chars)", len(raw or ""))
    return result
