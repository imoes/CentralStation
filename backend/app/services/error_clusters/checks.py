"""Prüfschritte eines Incidents ausführen — experimentell, nur per Knopf, nur lesend.

Ein Klick in der Incident-Ansicht (Link aus der Mail) führt die Schritte aus:
- die strukturierten Schritte der KI-Einschätzung (triage.validate_steps: nur
  cs_policy-Vorlagen, nur Hosts des Ausbruchs), oder — wenn eine ältere Einschätzung
  keine hat — eine Basisprüfung (Plattenplatz, Speicher/Last) der betroffenen Hosts.
Ausgeführt wird über den Prüf-Container (probe_client): Inventar-Prüfung, Protokoll in
remote_exec_log. Nichts läuft automatisch, nichts geht per Mail hinaus.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MAX_STEPS = 8
BASIS_HOSTS = 3
OUTPUT_CHARS = 4000
KEEP_RUNS = 3


def planned_steps(ctx: dict | None) -> tuple[list[dict], str]:
    """(Schritte, Herkunft) — "triage" oder "basis"."""
    ctx = ctx or {}
    auto = (ctx.get("triage") or {}).get("pruefschritte_auto") or []
    if auto:
        return auto[:MAX_STEPS], "triage"
    hosts = [h for h in (ctx.get("docker_hosts") or ctx.get("hosts") or []) if "." in h]
    steps = []
    for h in hosts[:BASIS_HOSTS]:
        steps += [{"host": h, "template": "disk_usage", "params": {}},
                  {"host": h, "template": "memory", "params": {}}]
    return steps[:MAX_STEPS], "basis"


def check_runs(inc) -> list[dict]:
    return [e for e in (inc.causal_context or []) if isinstance(e, dict) and e.get("type") == "check_run"]


async def run_checks(db, inc, user) -> dict:
    """Schritte ausführen, Ergebnis am Incident speichern (letzte KEEP_RUNS Läufe)."""
    from app.services.error_clusters.incident import cluster_context
    from app.services.probe_client import ProbeError, run_probe

    steps, origin = planned_steps(cluster_context(inc))
    results = []
    for st in steps:
        item = {"host": st["host"], "template": st["template"], "params": st.get("params") or {}}
        try:
            res = await run_probe(st["host"], st["template"], st.get("params") or {},
                                  user_id=str(user.id), source="checks",
                                  context={"incident_id": str(inc.id)}, timeout=45)
            out = res.get("stdout") or ""
            item.update(host=res.get("host", st["host"]), exit_code=res.get("exit_code"),
                        error=res.get("error"), output=out[:OUTPUT_CHARS],
                        truncated=bool(res.get("truncated")) or len(out) > OUTPUT_CHARS,
                        stderr=(res.get("stderr") or "")[:500] if res.get("error") else "",
                        duration_ms=res.get("duration_ms"))
        except ProbeError as exc:
            item.update(error=exc.kind, message=str(exc))
        results.append(item)
    run = {"type": "check_run", "at": datetime.now(timezone.utc).isoformat(),
           "by": getattr(user, "email", None), "origin": origin, "results": results}
    others = [e for e in (inc.causal_context or [])
              if not (isinstance(e, dict) and e.get("type") == "check_run")]
    inc.causal_context = others + (check_runs(inc) + [run])[-KEEP_RUNS:]
    inc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("incident %s: %d check step(s) run by %s (%s)", str(inc.id)[:8], len(results),
             getattr(user, "email", "?"), origin)
    return run


def prompt_lines(inc) -> list[str]:
    """Letzter Prüflauf fürs Konsolen-Briefing — knapp, mit Herkunft."""
    runs = check_runs(inc)
    if not runs:
        return []
    run = runs[-1]
    out = ["", f"## Prüfschritte (ausgeführt {run['at'][:16]} UTC von {run.get('by')}, nur lesend)"]
    for r in run["results"]:
        head = f"- {r['host']} · {r['template']} {r.get('params') or ''}".rstrip()
        if r.get("error"):
            out.append(f"{head}: FEHLER {r['error']} {r.get('message') or ''}".rstrip())
        else:
            out.append(f"{head} (exit {r.get('exit_code')}):")
            out.append("```\n" + (r.get("output") or "")[:1200] + "\n```")
    return out
