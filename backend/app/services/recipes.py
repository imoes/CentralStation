"""Rezepte: aus einer gelösten Konsolen-Sitzung lernen, was bei diesem Fehlermuster hilft.

Ablauf beim „✓ GELÖST" einer Incident-Sitzung:
1. Die tatsächlich AUSGEFÜHRTEN Werkzeugaufrufe holen (userenv /sessions/{sid}/tool-calls)
   — nicht den Chat-Text: festgehalten wird, was lief, nicht was erzählt wurde.
2. Das LLM ordnet die Lese-Befehle den cs_policy-Vorlagen zu und fasst Problem/Lösung
   zusammen. Was keiner Vorlage entspricht, bleibt Text (other_commands) und läuft nie.
3. Jeder Schritt wird gegen die Positivliste geprüft (render); Ungültiges fällt weg.
4. Gespeichert am Muster (LogTemplate.key) bzw. Host, gespiegelt als cs-knowledge-Runbook.

Angewendet wird ein Rezept beim nächsten Ausbruch desselben Musters: es steht in Incident,
Mail und Konsolen-Übergabe, und seine Schritte laufen über den Knopf „Prüfschritte
ausführen" (error_clusters/checks.py) — nie automatisch.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select

log = logging.getLogger(__name__)

MAX_STEPS = 6
MAX_OTHER = 12

SYSTEM = (
    "Du bist ein erfahrener SRE und dokumentierst einen GELÖSTEN Vorfall als Rezept für das "
    "nächste Mal. Du bekommst den Vorfall, eine Zusammenfassung des Gesprächs und die Liste "
    "der tatsächlich ausgeführten Befehle/Werkzeuge.\n"
    "Ordne die Befehle, die zur DIAGNOSE geholfen haben, den erlaubten Lese-Vorlagen zu "
    "('vorlagen': id + Parameter). Nur Vorlagen aus dieser Liste; host weglassen, wenn der "
    "Schritt für jeden betroffenen Host gilt. Befehle ohne passende Vorlage (auch alle "
    "schreibenden, z. B. Neustarts) nennst du in weitere_befehle — wörtlich, mit kurzem Zweck.\n"
    "Antworte ausschließlich mit JSON:\n"
    '{"titel": str (≤ 80 Zeichen), "problem": str (1–2 Sätze), "loesung": str (1–3 Sätze), '
    '"schritte": [{"vorlage": str, "parameter": {}, "host": str|null, "warum": str}], '
    '"weitere_befehle": [{"befehl": str, "zweck": str, "schreibend": bool}]}'
)


def scope_for(inc, cluster_key: str | None) -> tuple[str, str]:
    """Woran das Rezept hängt: an das Muster (auch Flotten-Service), sonst an den Host."""
    if cluster_key:
        return "template", cluster_key
    return "host", (inc.primary_host or "").lower()


async def cluster_key_of(db, inc) -> str | None:
    from app.models.error_cluster import ErrorCluster
    from app.services.error_clusters.incident import cluster_context
    ctx = cluster_context(inc) or {}
    if ctx.get("template_key"):
        return ctx["template_key"]
    cid = ctx.get("cluster_id")
    if not cid:
        return None
    try:
        ec = await db.get(ErrorCluster, uuid.UUID(cid))
    except ValueError:
        return None
    return ec.key if ec else None


def validate_recipe_steps(steps, hosts: list[str] | None) -> list[dict]:
    """Nur cs_policy-Vorlagen; ein fester Host muss zum Vorfall gehören, sonst entfällt er
    (der Schritt gilt dann für die betroffenen Hosts des jeweiligen Ausbruchs)."""
    from cs_policy.templates import PolicyError, render
    from app.services.error_clusters.incident import host_matches
    out = []
    for st in steps if isinstance(steps, list) else []:
        if not isinstance(st, dict):
            continue
        tpl = str(st.get("vorlage") or st.get("template") or "").strip()
        params = st.get("parameter") or st.get("params") or {}
        if not isinstance(params, dict):
            continue
        params = {k: str(v) for k, v in params.items()}
        try:
            render(tpl, params)
        except PolicyError:
            continue
        host = str(st.get("host") or "").strip() or None
        if host and hosts is not None and not host_matches(host, hosts):
            host = None
        out.append({"template": tpl, "params": params, "host": host,
                    "why": str(st.get("warum") or st.get("why") or "")[:200]})
        if len(out) >= MAX_STEPS:
            break
    return out


def parse_answer(raw: str, hosts: list[str] | None) -> dict | None:
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
    if not isinstance(data, dict) or not data.get("titel"):
        return None
    other = []
    for o in data.get("weitere_befehle") or []:
        if isinstance(o, dict) and o.get("befehl"):
            other.append({"command": str(o["befehl"])[:500], "purpose": str(o.get("zweck") or "")[:200],
                          "writes": bool(o.get("schreibend"))})
    return {"title": str(data["titel"])[:255], "problem": str(data.get("problem") or ""),
            "solution": str(data.get("loesung") or ""),
            "steps": validate_recipe_steps(data.get("schritte"), hosts),
            "other_commands": other[:MAX_OTHER]}


def materialize(recipe_steps: list[dict], hosts: list[str], max_hosts: int = 2) -> list[dict]:
    """Rezept-Schritte → ausführbare Schritte für DIESEN Ausbruch."""
    targets = [h for h in hosts if "." in h][:max_hosts]
    out = []
    for st in recipe_steps or []:
        for h in ([st["host"]] if st.get("host") else targets):
            out.append({"host": h, "template": st["template"], "params": st.get("params") or {}})
    return out


async def fetch_tool_calls(user_id: str, session_id: str) -> list[dict]:
    from app.api.computer_proxy import _internal_client, _target_url
    try:
        async with _internal_client(timeout=20.0) as c:
            r = await c.get(f"{_target_url(user_id)}/sessions/{session_id}/tool-calls")
        if r.status_code == 200:
            return r.json().get("calls") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("recipes: tool-calls for %s failed: %s", session_id[:8], exc)
    return []


def _calls_for_prompt(calls: list[dict]) -> list[str]:
    out = []
    for c in calls:
        tool = c.get("tool", "")
        if c.get("command"):
            out.append(f"{tool}: {c['command'][:300]}")
        elif tool and not tool.lower().startswith(("toolsearch", "tool_search", "todowrite")):
            out.append(f"{tool} {json.dumps(c.get('input') or {}, ensure_ascii=False)[:200]}")
    return out[:80]


async def distill(db, inc, *, session_id: str, user, summary: str) -> dict | None:
    """Rezept aus einer gelösten Sitzung bauen und speichern. None = nichts Brauchbares."""
    from cs_policy.templates import describe
    from app.models.recipe import Recipe
    from app.services.error_clusters.incident import cluster_context
    from app.services.llm_client import generate_text
    from app.services.settings import get_active_llm_config

    ctx = cluster_context(inc) or {}
    hosts = list(ctx.get("hosts") or []) or ([inc.primary_host] if inc.primary_host else [])
    calls = await fetch_tool_calls(str(user.id), session_id)
    payload = {
        "vorfall": inc.title, "muster": ctx.get("template"), "hosts": hosts[:15],
        "zusammenfassung_gespraech": summary[:2000],
        "ausgefuehrt": _calls_for_prompt(calls),
        "vorlagen": [{"id": t["id"], "zweck": t["description"],
                      "parameter": {n: p["hint"] for n, p in t["params"].items()}}
                     for t in describe() if t["id"] not in ("man_search", "man_read")],
    }
    cfg = await get_active_llm_config(db)
    if not cfg.is_configured:
        return None
    raw = await generate_text(cfg, [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                              temperature=0.1, reasoning_effort="low")
    parsed = parse_answer(raw, hosts)
    if not parsed:
        log.warning("recipes: unparseable answer (%d chars) for incident %s", len(raw or ""), str(inc.id)[:8])
        return None
    scope_type, scope_key = scope_for(inc, await cluster_key_of(db, inc))
    rec = Recipe(scope_type=scope_type, scope_key=scope_key, source_session_id=session_id,
                 source_incident_id=inc.id, created_by=user.id, **parsed)
    db.add(rec)
    await db.commit()
    log.info("recipes: stored '%s' for %s:%s (%d steps, %d other, %d calls seen)",
             parsed["title"][:40], scope_type, scope_key[:24], len(parsed["steps"]),
             len(parsed["other_commands"]), len(calls))
    try:
        from app.services import vi_konsole
        pruef = ("; Prüfschritte: " + "; ".join(f"{s['template']} {s['params'] or ''}".strip()
                                                for s in parsed["steps"])) if parsed["steps"] else ""
        r = await vi_konsole.lerne(
            db, f"Rezept: {parsed['title']}. Problem: {parsed['problem']} Lösung: {parsed['solution']}{pruef}",
            ["herkunft:rezept", f"rezept:{scope_type}", *([f"host:{inc.primary_host}"] if inc.primary_host else [])],
            f"CentralStation Rezept {rec.id} aus Konsolen-Sitzung {session_id}, Incident {inc.id}", 2, ausgang="gut")
        log.info("recipes: VI %s", r[:120])
    except Exception as exc:  # noqa: BLE001
        log.warning("recipes: VI mirror failed: %s", exc)
    return summary_of(rec)


async def latest_for(db, scope_type: str, scope_key: str | None):
    from app.models.recipe import Recipe
    if not scope_key:
        return None
    return (await db.execute(select(Recipe).where(Recipe.scope_type == scope_type,
                                                  Recipe.scope_key == scope_key)
                             .order_by(Recipe.created_at.desc()).limit(1))).scalar_one_or_none()


async def for_incident(db, inc):
    """Das jüngste Rezept zu Muster oder Host dieses Incidents (Muster hat Vorrang)."""
    key = await cluster_key_of(db, inc)
    rec = await latest_for(db, "template", key) if key else None
    if rec is None and inc.primary_host:
        rec = await latest_for(db, "host", inc.primary_host.lower())
    return rec


async def for_cluster(db, cluster_id: str | None):
    """Rezept zum Muster einer Episode (für die Mail, bevor der Incident angesehen wird)."""
    from app.models.error_cluster import ErrorCluster
    if not cluster_id:
        return None
    try:
        ec = await db.get(ErrorCluster, uuid.UUID(cluster_id))
    except ValueError:
        return None
    return await latest_for(db, "template", ec.key) if ec else None


def summary_of(rec) -> dict[str, Any] | None:
    if rec is None:
        return None
    return {"id": str(rec.id), "title": rec.title, "problem": rec.problem, "solution": rec.solution,
            "steps": rec.steps or [], "other_commands": rec.other_commands or [],
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "scope": f"{rec.scope_type}:{rec.scope_key}", "applied_count": rec.applied_count}


def prompt_lines(rec_summary: dict | None) -> list[str]:
    if not rec_summary:
        return []
    out = ["", f"## Bewährtes Rezept (aus einem früheren, gelösten Vorfall: {rec_summary['title']})",
           f"Problem damals: {rec_summary.get('problem') or '-'}",
           f"Lösung damals: {rec_summary.get('solution') or '-'}"]
    for st in rec_summary.get("steps") or []:
        out.append(f"- Prüfschritt: {st['template']} {st.get('params') or ''} "
                   f"{'auf ' + st['host'] if st.get('host') else '(betroffene Hosts)'} — {st.get('why') or ''}")
    for o in rec_summary.get("other_commands") or []:
        mark = " [SCHREIBEND — nur mit Freigabe]" if o.get("writes") else ""
        out.append(f"- Damals außerdem: `{o['command']}` — {o.get('purpose') or ''}{mark}")
    return out
