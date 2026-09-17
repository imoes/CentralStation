"""Generative dashboard designer — the AI *composes* a bespoke dashboard.

Unlike layout_engine.py (which only reorders a fixed set of widgets), this
service analyses the full operational situation — including the metrics and
forecasts collected into cs-metrics-checkmk — and asks the LLM to design a
brand-new set of widgets tailored to the current situation: which widget types,
which queries, which layout.

Pipeline:
  1. gather_situation()   — severity counts, latest AI findings, worklist,
                            fleet vitals + forecast candidates (host/service/
                            metric_id resolved from real metrics, so the LLM
                            cannot hallucinate metric ids)
  2. _ask_llm()           — one LLM call → JSON {rationale, widgets:[...]}
  3. _validate_widgets()  — drop unknown types / bad configs, resolve metric
                            widgets against the real candidates, clamp + pack
                            the grid. Never trusts the model blindly.
  4. fallback             — if the LLM fails or returns nothing usable, build a
                            deterministic set (counts + ai_summary + top_hosts +
                            the acute forecast widgets) so the dashboard is
                            always populated.

The result is a list of widget specs (type/title/grid/config) + a rationale.
The API layer persists them as real DashboardWidget rows so the existing
/dashboard-widgets/{id}/data renderer handles everything — no new render code.
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import Any

from app.services.ai_language import with_language

log = logging.getLogger(__name__)

# CUE production host detection:
# Hosts with "cue" in their name are critical for the publishing group.
# Hosts with "stage" or "test" in their FQDN are non-production and excluded.
_CUE_PROD_RE = _re.compile(r'cue', _re.IGNORECASE)
_NON_PROD_RE = _re.compile(r'stage|test', _re.IGNORECASE)


def _is_cue_prod(host: str) -> bool:
    """True if host is a CUE production system (has 'cue' but not 'stage'/'test')."""
    return bool(_CUE_PROD_RE.search(host)) and not bool(_NON_PROD_RE.search(host))


# Disk/RAM threshold above which a vital is shown even without a forecast candidate.
# Below this threshold AND not trending → stable-high → filter out of LLM context.
_STABLE_METRIC_ACUTE_PCT = 90.0

COLS = 12  # GridStack columns

# Reserved name of the per-user AI-composed dashboard. This — NOT the `mode`
# column alone — uniquely identifies the generative singleton, so the scheduler
# and the dashboard-list filter never touch a user's hand-built dashboard even
# if its mode column was mislabelled by an earlier version.
GENERATIVE_DASHBOARD_NAME = "🪄 KI-Lagebild"

# Widget types the designer may emit and the config keys each accepts.
_ALLOWED_CONFIG_KEYS: dict[str, set[str]] = {
    "stat":       {"index_pattern", "query_string"},
    "list":       {"index_pattern", "query_string", "limit"},
    "donut":      {"index_pattern", "query_string"},
    "bar":        {"index_pattern", "query_string", "agg_field", "limit"},
    "top_hosts":  {"index_pattern", "query_string", "limit"},
    "gauge":      {"index_pattern", "query_string", "total_query_string", "unit", "warn", "critical"},
    "ai_summary": {"agent_type"},
    "war_room":   {"agent_type"},
    "incidents":  {"limit"},
    "timeseries": {"data_source", "host", "hosts", "service", "metric_id", "graph_index", "hours", "unit"},
    "forecast":   {"host", "service", "metric_id", "graph_index", "history_hours", "horizon_hours", "unit"},
}

# Default grid size per type (w, h)
_DEFAULT_SIZE: dict[str, tuple[int, int]] = {
    "stat":       (3, 2),
    "donut":      (4, 4),
    "list":       (6, 5),
    "bar":        (5, 4),
    "top_hosts":  (4, 4),
    "gauge":      (3, 3),
    "ai_summary": (6, 4),
    "war_room":   (12, 5),
    "incidents":  (6, 5),
    "timeseries": (6, 6),
    "forecast":   (6, 6),   # needs room for history + forecast line
}

# Map a bridge forecast/vital metric id to its CheckMK service description.
_METRIC_SERVICE: dict[str, str] = {
    "fs_used_percent":  "Filesystem /",
    "mem_used_percent": "Memory",
    "load1":            "CPU load",
    "load5":            "CPU load",
}


async def _get_scoped_severity_counts(os_client: Any, host_scope: list[str]) -> dict[str, int] | None:
    """Severity counts constrained to the user's selected CheckMK host scope."""
    from datetime import datetime, timezone, timedelta

    hosts = [h for h in host_scope if h]
    if not hosts:
        return None
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    try:
        resp = await os_client.search(
            index="cs-feed-*",
            body={
                "query": {
                    "bool": {
                        "must": [{"range": {"created_at": {"gte": since}}}],
                        "filter": [{
                            "bool": {
                                "should": [
                                    {"terms": {"metadata.host.keyword": hosts}},
                                    {"terms": {"metadata.agent.keyword": hosts}},
                                    {"terms": {"metadata.host_candidates.keyword": hosts}},
                                ],
                                "minimum_should_match": 1,
                            }
                        }],
                        "must_not": [{"term": {"status": "resolved"}}],
                    }
                },
                "aggs": {"by_sev": {"terms": {"field": "severity", "size": 10}}},
                "size": 0,
            },
            ignore_unavailable=True,
        )
        return {
            b["key"]: b["doc_count"]
            for b in resp.get("aggregations", {}).get("by_sev", {}).get("buckets", [])
        }
    except Exception as e:
        log.debug("generative_designer: scoped severity counts failed: %s", e)
        return None


# ── 1. Situation gathering ──────────────────────────────────────────────────

async def gather_situation(db: Any, user_id: str | None = None) -> dict:
    """Collect the full operational picture for the LLM prompt.

    Reuses the bridge metrics math and the layout-engine severity counts so the
    generative dashboard sees exactly what the cockpit sees."""
    from app.core.opensearch import get_opensearch
    from app.services.dashboard.layout_engine import _get_severity_counts
    from app.api.bridge import _compute_metrics
    from app.services.feed_index import get_user_checkmk_host_scope
    from app.services.worklist_builder import get_latest_worklist
    from app.models.ai import AiAnalysis
    from sqlalchemy import select
    from datetime import datetime, timezone

    os_client = get_opensearch()
    source_state = {
        "severity_counts": "available",
        "analysis": "available",
        "metrics": "available",
        "worklist": "available",
        "incidents": "available",
    }
    host_scope = await get_user_checkmk_host_scope(db, user_id) if user_id else []
    host_scope_lc = {h.lower() for h in host_scope if h}

    # Severity counts (last hour, scoped to the user's selected sites when set)
    sev_counts = await _get_scoped_severity_counts(os_client, host_scope) if host_scope else await _get_severity_counts()
    if sev_counts is None:
        source_state["severity_counts"] = "unavailable"
        sev_counts = {}

    # Latest sysadmin analysis findings
    findings: list[dict] = []
    severity_summary = "none"
    analysis = None
    try:
        r = await db.execute(
            select(AiAnalysis)
            .where(AiAnalysis.agent_type == "sysadmin")
            .order_by(AiAnalysis.run_at.desc())
            .limit(1)
        )
        analysis = r.scalar_one_or_none()
        if analysis:
            severity_summary = analysis.severity_summary or "none"
            findings = [
                {
                    "title": f.get("title", ""),
                    "severity": f.get("severity", "info"),
                    "host": f.get("host") or f.get("affected_service") or "",
                    "source": f.get("source", ""),
                }
                for f in (analysis.findings or [])
                if not host_scope_lc
                or ((f.get("host") or f.get("affected_service") or "").lower() in host_scope_lc)
            ]
            findings = findings[:6]
            if host_scope_lc:
                sev_order = ["critical", "high", "medium", "low", "info"]
                severity_summary = next(
                    (sev for sev in sev_order if any(f.get("severity") == sev for f in findings)),
                    "none",
                )
        else:
            source_state["analysis"] = "unknown"
    except Exception as e:
        source_state["analysis"] = "unavailable"
        log.debug("gather_situation: findings failed: %s", e)

    # Fleet vitals + forecast candidates from cs-metrics-checkmk
    vitals: list[dict] = []
    forecast_candidates: list[dict] = []
    try:
        raw_vitals, raw_forecasts = await _compute_metrics(os_client, host_scope)
        # Enrich vitals with the CheckMK service so the LLM can build timeseries
        for v in raw_vitals:
            service = _METRIC_SERVICE.get(v.get("metric", ""), "")
            vitals.append({
                "host": v["host"], "metric_id": v["metric"], "service": service,
                "label": v.get("label", ""), "value": v.get("value"),
                "unit": v.get("unit", ""),
            })
        # Forecast candidates carry the exact (host, service, metric_id) tuple
        # so the LLM can reference a *valid* forecast widget — no hallucination.
        for f in raw_forecasts:
            service = _METRIC_SERVICE.get(f.get("metric", ""), "")
            if not service:
                continue
            forecast_candidates.append({
                "host": f["host"],
                "service": service,
                "metric_id": f["metric"],
                "label": f.get("label", ""),
                "current": f.get("current"),
                "threshold": f.get("threshold"),
                "eta_hours": f.get("eta_hours"),
            })
    except Exception as e:
        source_state["metrics"] = "unavailable"
        log.debug("gather_situation: metrics failed: %s", e)

    # Worklist top items
    worklist_items: list[dict] = []
    try:
        wl = await get_latest_worklist(db)
        if wl:
            scoped_items = [
                item for item in (wl.get("items") or [])
                if not host_scope_lc or (item.get("host") or "").lower() in host_scope_lc
            ]
            for item in scoped_items[:6]:
                worklist_items.append({
                    "host": item.get("host", ""),
                    "title": item.get("title", ""),
                    "severity": item.get("severity", ""),
                    "source": item.get("source", ""),
                })
        else:
            source_state["worklist"] = "unknown"
    except Exception as e:
        source_state["worklist"] = "unavailable"
        log.debug("gather_situation: worklist failed: %s", e)

    # Top recommendations from the latest analysis (max 3, sorted by priority).
    # These were previously invisible to the generative LLM — now passed so the
    # LLM can align widget selection with the recommended next actions.
    _SEV_ORDER = ["critical", "high", "medium", "low", "info", "none"]
    top_recommendations: list[dict] = []
    try:
        if analysis and analysis.recommendations:
            sorted_recs = sorted(
                analysis.recommendations or [],
                key=lambda r: _SEV_ORDER.index(r.get("priority", "none"))
                    if r.get("priority", "none") in _SEV_ORDER else 99
            )
            for r in sorted_recs[:3]:
                top_recommendations.append({
                    "action": r.get("action", ""),
                    "priority": r.get("priority", "medium"),
                })
    except Exception as e:
        log.debug("gather_situation: recommendations failed: %s", e)

    # CUE production hosts currently in findings/worklist.
    # These are publishing-group systems that need special treatment.
    cue_production_hosts = sorted({
        h for h in
        [f.get("host", "") for f in findings] +
        [w.get("host", "") for w in worklist_items]
        if h and _is_cue_prod(h)
    })

    # Filter out stale-high Disk/RAM vitals that are NOT trending toward threshold.
    # A stable 87 % RAM that hasn't moved in hours is NOT worth a dashboard widget.
    # Rule: keep a Disk/RAM vital only if it's a forecast candidate (actively trending)
    #       OR if it's acutely high (>= _STABLE_METRIC_ACUTE_PCT, e.g. 90 %).
    _fc_keys = {(c["host"], c["metric_id"]) for c in forecast_candidates}
    filtered_vitals: list[dict] = []
    for v in vitals:
        m = v.get("metric_id", "")
        key = (v["host"], m)
        val = float(v.get("value") or 0)
        if m in ("fs_used_percent", "mem_used_percent"):
            if key in _fc_keys or val >= _STABLE_METRIC_ACUTE_PCT:
                filtered_vitals.append(v)
            # else: stable-high but not trending → silently drop
        else:
            # CPU and any future metrics always shown
            filtered_vitals.append(v)

    # Open incidents (cross-source, multi-alert groups)
    open_incidents: list[dict] = []
    try:
        from sqlalchemy import select, desc, func as sa_func
        from app.models.workflow import Incident, IncidentMember
        inc_rows = await db.execute(
            select(Incident)
            .where(Incident.status.in_(("open", "investigating")))
            .order_by(desc(Incident.updated_at))
            .limit(5)
        )
        for inc in inc_rows.scalars().all():
            if host_scope_lc and (inc.primary_host or "").lower() not in host_scope_lc:
                continue
            cnt = await db.execute(
                select(sa_func.count()).where(IncidentMember.incident_id == inc.id)
            )
            open_incidents.append({
                "id": str(inc.id),
                "host": inc.primary_host,
                "severity": inc.severity,
                "title": inc.title,
                "member_count": cnt.scalar() or 0,
            })
    except Exception as e:
        source_state["incidents"] = "unavailable"
        log.debug("gather_situation: open_incidents failed: %s", e)

    return {
        "severity_counts": sev_counts,
        "severity_summary": severity_summary,
        "host_scope": host_scope,
        "findings": findings,
        "vitals": filtered_vitals,
        "forecast_candidates": forecast_candidates,
        "worklist": worklist_items,
        "cue_production_hosts": cue_production_hosts,
        "top_recommendations": top_recommendations,
        "open_incidents": open_incidents,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source_state": source_state,
    }


# ── 2. LLM composition ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Du bist Dashboard-Designer für ein IT-Operations-Cockpit (CentralStation).
Komponiere ein maßgeschneidertes Dashboard, das die AKTUELLE Lage optimal abbildet.

GRID-DIMENSIONEN UND FLÄCHENBUDGET:
Das Dashboard hat 12 Spalten (gs_w 1–12). Jede Höheneinheit (gs_h) = 80px.
Sichtbarer Viewport ohne Scrollen: ca. 9 Zeilen (720px). Nutze MAXIMAL 18 Zeilen gesamt.
Gesamt-Flächenbudget: 80–110 Einheiten (gs_w × gs_h summiert über alle Widgets).

Empfohlene Standardgrößen (gs_w × gs_h = Fläche):
  stat        3×2 =  6   (kleine Zahl, kompakt)
  donut       4×4 = 16   (Donut-Chart)
  list        6×5 = 30   (Alert-Liste, braucht Platz)
  bar         5×4 = 20   (Balkendiagramm)
  top_hosts   4×4 = 16   (Host-Ranking)
  ai_summary  6×4 = 24   (KI-Lagebericht)
  war_room   12×5 = 60   (Blast-Radius, VOLLE BREITE, nur bei critical/high)
  timeseries  6×6 = 36   (Zeitreihen-Chart, braucht Höhe)
  forecast    6×6 = 36   (Prognose-Chart, braucht Höhe)

PRÜFUNG vor der Ausgabe:
✓ Summieren sich gs_w aller Widgets in einer Zeile auf ≤ 12?
✓ Gesamtfläche aller Widgets ≤ 110 Einheiten?
✓ Keine Widget-Überlappungen (gs_x + gs_w ≤ 12)?

NUTZE NUR diese Widget-Typen mit exakt diesen config-Schlüsseln:
- "stat": {"index_pattern":"cs-feed-*","query_string":"<lucene>"}  — eine große Kennzahl
- "list": {"index_pattern":"cs-feed-*","query_string":"<lucene>","limit":15}  — Alert-Liste
- "donut": {"index_pattern":"cs-feed-*","query_string":"<lucene>"}  — Severity-Verteilung
- "bar": {"index_pattern":"cs-feed-*","query_string":"<lucene>","agg_field":"source","limit":10}  — agg_field MUSS einer dieser Werte sein: "source", "severity", "host"
- "top_hosts": {"index_pattern":"cs-feed-*","query_string":"<lucene>","limit":8}  — Problem-Hosts
- "gauge": {"index_pattern":"cs-feed-*","query_string":"<zähler-lucene>","total_query_string":"<nenner-lucene>","warn":70,"critical":90}  — Anteil/Quote in %. query_string = Zähler (z.B. "severity:critical AND NOT status:resolved"), total_query_string = Nenner/Grundgesamtheit (z.B. "NOT status:resolved"). Nur nutzen wenn eine Quote AUSSAGEKRÄFTIG ist (z.B. Anteil kritischer Alerts); sonst "stat" bevorzugen.
- "ai_summary": {"agent_type":"sysadmin"}  — KI-Lagebericht
- "war_room": {"agent_type":"sysadmin"}  — Blast-Radius (NUR bei critical/high)
- "timeseries": {"data_source":"checkmk","host":"<host>","service":"<service>","metric_id":"<metric_id>","hours":4}
- "forecast": {"host":"<host>","service":"<service>","metric_id":"<metric_id>","history_hours":72,"horizon_hours":24}

DENKPROZESS — überlege VOR der JSON-Ausgabe (kein Output nötig, nur intern):
1. Was ist das DRINGENDSTE Problem? Welcher Widget-Typ zeigt es am deutlichsten?
2. Was ERGÄNZT die Hauptaussage sinnvoll? (max. 2 Ergänzungen)
3. Gibt es forecast_candidates? → forecast-Widget PFLICHT, zähle zur Fläche.
4. Wieviel Fläche kostet mein Plan? Passe Größen an bis Budget ≤ 110 stimmt.
5. Prüfe Zeilensummen: jede Zeile darf gs_w-Summe = 12 nicht überschreiten.

REGELN:
- Lucene query_string: Standard immer "NOT status:resolved" anhängen.
- WICHTIG: forecast_candidates → forecast-Widget mit EXAKT deren host/service/metric_id.
- Für "timeseries" nur vitals-Einträge verwenden — host, service, metric_id unverändert kopieren.
- Bei critical/high: ai_summary und/oder war_room oben platzieren.
- Bei ruhiger Lage: kompakteres Dashboard (Counts + Liste + Donut).
- CUE-Produktionshosts: Wenn cue_production_hosts gefüllt, MÜSSEN diese prominent erscheinen.
- Stabile Metriken ignorieren: KEINE timeseries/forecast für Disk/RAM außerhalb forecast_candidates.
- top_recommendations: Empfehlungen auf spezifische Hosts → passende list/top_hosts priorisieren.
- open_incidents: Wenn offene Incidents vorhanden, MUSS ein Widget type="incidents" oben erscheinen
  (gs_w=12, gs_h=3). Incidents sind cross-source-korrelierte Alert-Gruppen — höchste Priorität.

Die "rationale" ist ein präzises LAGE-BRIEFING:
- Severity/Anzahl (z.B. "3 critical, 8 high")
- 2–4 wichtigste Hosts mit Problem
- Metrik-Werte mit ETA für Forecast-Kandidaten
KEINE Widget-Namen. Maximal 4 Sätze.

FINALE AUSGABE: Gib genau dieses JSON aus:
{"rationale":"<Lage-Briefing>",
 "widgets":[{"type":"...","title":"...","gs_x":0,"gs_y":0,"gs_w":4,"gs_h":3,"config":{...}}]}"""


_THINK_RE = _re.compile(r'<think>.*?</think>', _re.DOTALL | _re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    """Remove Qwen3 <think>...</think> blocks before JSON parsing."""
    return _THINK_RE.sub('', text).strip()


def _find_code_block_contents(text: str, lang: str = "json") -> list[str]:
    """Extract all content from ```<lang>...``` blocks using a manual scanner.

    Robust against: multiple code blocks in thinking, indented fences, backticks
    inside JSON string values. Returns all found content blocks in order."""
    opener = f"```{lang}"
    results: list[str] = []
    i = 0
    while i < len(text):
        idx = text.find(opener, i)
        if idx == -1:
            break
        line_end = text.find('\n', idx + len(opener))
        if line_end == -1:
            break
        content_start = line_end + 1
        rest = text[content_start:]
        # Closing ``` must appear at the start of a line (optionally indented)
        close_match = _re.search(r'(?m)^[ \t]*```[ \t]*$', rest)
        if not close_match:
            i = content_start
            continue
        content = rest[:close_match.start()].strip()
        if content:
            results.append(content)
        i = content_start + close_match.end()
    return results


async def _ask_llm(db: Any, situation: dict, lang: str) -> dict | None:
    """One LLM call → parsed {rationale, widgets}. Returns None on any failure.

    Enables Qwen3 thinking mode (enable_thinking=True, budget=1500) so the model
    reasons about widget selection before emitting JSON. Thinking output is stripped
    before parsing so _parse_json only sees clean JSON.
    """
    from app.services.settings import get_active_llm_config
    from app.services.llm_client import generate_text, LLMInvocationError

    # Use the ACTIVE provider (Codex when selected) so the generative dashboard
    # benefits from the chosen model.
    llm_cfg = await get_active_llm_config(db)
    if not llm_cfg.is_configured:
        log.info("generative_designer: no LLM configured → fallback")
        return None

    if getattr(llm_cfg, "api_mode", "chat_completions") != "chat_completions":
        # Codex/Claude: enable thinking → maps to reasoning_effort or extended thinking.
        llm_cfg.thinking_mode = True

    log.info(
        "generative_designer: calling LLM api_mode=%s model=%s timeout=%s",
        getattr(llm_cfg, "api_mode", "?"),
        getattr(llm_cfg, "model", "?"),
        getattr(llm_cfg, "timeout_seconds", "?"),
    )

    user_msg = "Aktuelle Lage:\n" + json.dumps(situation, ensure_ascii=False, indent=0)
    try:
        raw = await generate_text(
            llm_cfg,
            [
                {"role": "system", "content": with_language(_SYSTEM_PROMPT, lang)},
                {"role": "user", "content": user_msg},
            ],
            reasoning_effort="medium",
            temperature=0.3,
            # Assistant prefill: forces Qwen3 A3B to emit JSON immediately (no planning
            # monologue). Applied provider-aware inside generate_text.
            json_prefill="```json\n{",
            max_output_tokens=3000,
        )
    except LLMInvocationError as e:
        log.warning("generative_designer: LLM call failed: %s", e)
        return None
    except Exception as e:
        import traceback as _tb
        log.warning("generative_designer: LLM error [%s]: %s\n%s", type(e).__name__, e, _tb.format_exc())
        return None

    # Strip <think>…</think> blocks (some models). Qwen3 A3B uses plain prose,
    # so this is a no-op for that model, but keeps other providers clean.
    clean = _strip_thinking(raw)

    parsed = _parse_json(clean)
    if not parsed or not isinstance(parsed.get("widgets"), list):
        log.warning("generative_designer: LLM returned no usable widgets (raw[:200]=%s)", raw[:200])
        return None
    return parsed


# Markers after which the LLM tends to dump a full host list — cut the rationale
# there so the briefing stays a short situational summary.
_RATIONALE_CUT_MARKERS = (
    "betroffene hosts", "betroffene systeme", "affected hosts",
    "vollständige liste", "hosts vollständig", "host-liste",
)


def _clean_rationale(text: str) -> str:
    """Trim the LLM rationale to a short situational briefing.

    Removes any trailing 'Betroffene Hosts: …' style dump and caps length."""
    t = (text or "").strip()
    low = t.lower()
    cut = len(t)
    for marker in _RATIONALE_CUT_MARKERS:
        i = low.find(marker)
        if i != -1:
            cut = min(cut, i)
    t = t[:cut].strip().rstrip(",;:.").strip()
    # If the cut left a dangling sentence opener, also drop a trailing connector
    return (t + ".") if t and not t.endswith((".", "!", "?")) else t


def _parse_json(raw: str) -> dict | None:
    """Tolerant JSON extraction — handles code fences and embedded JSON in thinking.

    For Qwen3 A3B (llama.cpp bug): the full model response (thinking narrative +
    JSON answer) lands in reasoning_content. The model writes the final JSON inside
    a ```json code block. Use a manual scanner to find it robustly."""
    if not raw:
        return None
    text = raw.strip()

    # 1. Scan all ```json...``` blocks with _find_code_block_contents and prefer the
    #    LAST one (models verify/rewrite JSON at the end of their thinking).
    for content in reversed(_find_code_block_contents(text, "json")):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict) and "widgets" in obj:
                return obj
        except Exception:
            continue

    # 2. Also try unmarked ``` blocks (some models omit the language tag).
    for content in reversed(_find_code_block_contents(text, "")):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict) and "widgets" in obj:
                return obj
        except Exception:
            continue

    # 3. Try the whole text directly (clean JSON output from Codex/Claude).
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "widgets" in obj:
            return obj
    except Exception:
        pass

    # 4. Scan right-to-left: find the last complete {…} object with "widgets"
    #    (handles cases where code block delimiters are missing).
    pos = len(text)
    while pos > 0:
        end = text.rfind("}", 0, pos)
        if end == -1:
            break
        depth = 0
        start = -1
        for i in range(end, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start = i
                    break
        if start == -1:
            pos = end
            continue
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict) and "widgets" in obj:
                return obj
        except Exception:
            pass
        pos = end
    return None


# ── 3. Validation + grid packing ────────────────────────────────────────────

def semantic_widget_key(spec: dict) -> tuple:
    """Return the operational fact represented by a widget specification."""
    wtype = spec.get("widget_type") or spec.get("type") or ""
    cfg = spec.get("config") or {}
    if wtype in ("forecast", "timeseries"):
        return (
            "metric", str(cfg.get("host") or "").lower(),
            str(cfg.get("service") or "").lower(), str(cfg.get("metric_id") or "").lower(),
        )
    if wtype == "incidents":
        return ("incidents",)
    if wtype in ("ai_summary", "war_room"):
        return ("briefing",)
    return (
        wtype,
        str(cfg.get("index_pattern") or "").lower(),
        " ".join(str(cfg.get("query_string") or "").lower().split()),
        str(cfg.get("agg_field") or "").lower(),
    )


def _validate_widgets(raw_widgets: list, situation: dict, *, enforce_budget: bool = True) -> list[dict]:
    """Keep only well-formed widgets with valid configs; resolve metric widgets
    against the real candidates. Returns clean specs (grid packed later)."""
    # Valid metric reference sets
    forecast_keys = {
        (c["host"], c["service"], c["metric_id"])
        for c in situation.get("forecast_candidates", [])
    }
    # Map (host, service) → metric_id from the real vitals so timeseries widgets
    # always carry the exact metric_id (the working CheckMK lookup path).
    vital_metric_by_hs: dict[tuple, str] = {
        (v["host"], v["service"]): v.get("metric_id", "")
        for v in situation.get("vitals", []) if v.get("service")
    }

    clean: list[dict] = []
    for w in raw_widgets:
        if not isinstance(w, dict):
            continue
        wtype = str(w.get("type") or w.get("widget_type") or "").strip()
        if wtype not in _ALLOWED_CONFIG_KEYS:
            continue
        title = str(w.get("title") or wtype).strip()[:100]
        cfg_in = w.get("config") if isinstance(w.get("config"), dict) else {}
        # Keep only allowed keys for this type
        cfg = {k: v for k, v in cfg_in.items() if k in _ALLOWED_CONFIG_KEYS[wtype]}

        # Type-specific resolution / sanity
        if wtype in ("stat", "list", "donut", "bar", "top_hosts"):
            cfg.setdefault("index_pattern", "cs-feed-*")
            cfg.setdefault("query_string", "NOT status:resolved")
            if wtype in ("list", "top_hosts", "bar"):
                cfg["limit"] = int(cfg.get("limit") or (15 if wtype == "list" else 8))
            if wtype == "bar":
                # Only allow known single-field aggregations (reject "host|severity" etc.)
                _VALID_AGG = {"source", "severity", "host", "metadata.host", "hostgroup"}
                raw_agg = str(cfg.get("agg_field") or "source")
                cfg["agg_field"] = raw_agg if raw_agg in _VALID_AGG else "source"
        elif wtype == "gauge":
            cfg.setdefault("index_pattern", "cs-feed-*")
            cfg.setdefault("query_string", "severity:critical AND NOT status:resolved")
            cfg["total_query_string"] = str(cfg.get("total_query_string") or "NOT status:resolved")
            cfg["unit"] = str(cfg.get("unit") or "%")
            warn = int(cfg.get("warn") or 70)
            crit = int(cfg.get("critical") or 90)
            cfg["warn"] = min(max(warn, 0), 100)
            cfg["critical"] = min(max(crit, 0), 100)
        elif wtype in ("ai_summary", "war_room"):
            cfg["agent_type"] = cfg.get("agent_type") or "sysadmin"
        elif wtype == "incidents":
            cfg["limit"] = min(max(int(cfg.get("limit") or 5), 1), 20)
        elif wtype == "forecast":
            key = (cfg.get("host"), cfg.get("service"), cfg.get("metric_id"))
            if key not in forecast_keys:
                log.debug("generative_designer: dropping forecast widget (unknown %s)", key)
                continue
            cfg["history_hours"] = int(cfg.get("history_hours") or 72)
            cfg["horizon_hours"] = int(cfg.get("horizon_hours") or 24)
            # Guarantee hostname in title
            host_short = (cfg.get("host") or "").split(".")[0]
            if host_short and host_short not in title:
                title = f"{title} · {host_short}"
        elif wtype == "timeseries":
            cfg["data_source"] = "checkmk"
            key = (cfg.get("host"), cfg.get("service"))
            if key not in vital_metric_by_hs:
                log.debug("generative_designer: dropping timeseries widget (unknown %s)", key)
                continue
            # Always carry the exact metric_id from the real vitals so the widget
            # uses CheckMK's metric_id lookup (graph_index alone returns HTTP 400).
            cfg["metric_id"] = vital_metric_by_hs[key]
            cfg["hours"] = int(cfg.get("hours") or 4)
            # Guarantee hostname in title
            host_short = (cfg.get("host") or "").split(".")[0]
            if host_short and host_short not in title:
                title = f"{title} · {host_short}"

        clean.append({"widget_type": wtype, "title": title, "config": cfg})

    if not enforce_budget:
        return clean

    # One visible representation per operational fact. The rationale banner is the
    # single situation briefing, so a second ai_summary widget is redundant. When
    # incidents exist they own their member alerts and replace a parallel war-room
    # or generic active-alert list.
    has_incidents = bool(situation.get("open_incidents"))
    ranked = sorted(
        enumerate(clean),
        key=lambda item: (
            {
                "incidents": 0, "forecast": 1, "stat": 2, "gauge": 3,
                "top_hosts": 4, "donut": 5, "bar": 6, "timeseries": 7,
                "list": 8, "war_room": 9, "ai_summary": 10,
            }.get(item[1]["widget_type"], 20),
            item[0],
        ),
    )
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for _, spec in ranked:
        wtype = spec["widget_type"]
        if wtype in ("ai_summary", "war_room"):
            continue
        if has_incidents and wtype == "list":
            continue
        key = semantic_widget_key(spec)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)

    # Hard area budget. This is the final validator for LLM, fallback and injected
    # forecast widgets alike; there is no overflow allowance.
    _AREA_BUDGET = 110
    budgeted: list[dict] = []
    area_used = 0
    for spec in deduped:
        w_size, h_size = _DEFAULT_SIZE.get(spec["widget_type"], (4, 3))
        area = w_size * h_size
        if area_used + area <= _AREA_BUDGET:
            budgeted.append(spec)
            area_used += area
        else:
            log.debug("generative_designer: dropping %s (area=%d, used=%d > budget=%d)",
                      spec["widget_type"], area, area_used, _AREA_BUDGET)
    return budgeted


def _pack_grid(specs: list[dict]) -> list[dict]:
    """Assign gs_x/gs_y/gs_w/gs_h by row packing into a 12-col grid."""
    placed: list[dict] = []
    x = 0
    y = 0
    row_h = 0
    for s in specs:
        w, h = _DEFAULT_SIZE.get(s["widget_type"], (4, 3))
        w = min(w, COLS)
        if x + w > COLS:
            # next row
            y += row_h
            x = 0
            row_h = 0
        s["gs_x"], s["gs_y"], s["gs_w"], s["gs_h"] = x, y, w, h
        x += w
        row_h = max(row_h, h)
        placed.append(s)
    return placed


# ── 4. Fallback ─────────────────────────────────────────────────────────────

def _fallback_widgets(situation: dict, lang: str) -> tuple[list[dict], str]:
    """Deterministic dashboard when the LLM is unavailable / unusable.

    Always includes the acute forecast widgets so the metrics still drive the
    composition even without an LLM."""
    specs: list[dict] = [
        {"widget_type": "stat", "title": "Kritisch" if lang == "de" else "Critical",
         "config": {"index_pattern": "cs-feed-*", "query_string": "severity:critical AND NOT status:resolved"}},
        {"widget_type": "stat", "title": "Hoch" if lang == "de" else "High",
         "config": {"index_pattern": "cs-feed-*", "query_string": "severity:high AND NOT status:resolved"}},
        {"widget_type": "stat", "title": "Gesamt" if lang == "de" else "Total",
         "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved"}},
        {"widget_type": "incidents", "title": "Offene Incidents" if lang == "de" else "Open incidents",
         "config": {"limit": 5}},
        {"widget_type": "top_hosts", "title": "Top Problem-Hosts" if lang == "de" else "Top problem hosts",
         "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved", "limit": 8}},
    ]
    sev = situation.get("severity_summary", "none")
    # The acute forecast candidates → one forecast widget each (max 2)
    for c in situation.get("forecast_candidates", [])[:2]:
        specs.append({
            "widget_type": "forecast",
            "title": (
                f"Prognose {c.get('label','')} · {c['host'].split('.')[0]}"
                if lang == "de"
                else f"Forecast {c.get('label','')} · {c['host'].split('.')[0]}"
            ),
            "config": {
                "host": c["host"], "service": c["service"], "metric_id": c["metric_id"],
                "history_hours": 72, "horizon_hours": 24,
            },
        })
    n_fc = len(situation.get("forecast_candidates", [])[:2])
    if n_fc:
        host_list = ", ".join(c["host"] for c in situation.get("forecast_candidates", [])[:2])
        rationale = (
            f"Automatisches Lagebild: {sev}-Lage, {n_fc} Host(s) laufen laut "
            f"Metrik-Prognose auf einen Schwellwert zu ({host_list}) — Prognose-Widgets ergänzt."
            if lang == "de"
            else
            f"Automatic situation view: {sev} state, {n_fc} host(s) are trending toward a threshold "
            f"according to the metric forecast ({host_list}) — forecast widgets added."
        )
    else:
        rationale = (
            f"Automatisches Lagebild ({sev}-Lage) — KI-Komposition nicht verfügbar, Standardauswahl."
            if lang == "de"
            else f"Automatic situation view ({sev} state) — AI composition unavailable, using the default selection."
        )
    return specs, rationale


def _situation_hosts(situation: dict) -> list[str]:
    hosts: set[str] = set()
    for key in ("findings", "vitals", "forecast_candidates", "worklist"):
        for item in situation.get(key, []) or []:
            host = str(item.get("host") or "").strip()
            if host:
                hosts.add(host)
    return sorted(hosts, key=str.lower)


def _rationale_with_hosts(rationale: str, situation: dict) -> str:
    hosts = _situation_hosts(situation)
    if not hosts:
        return rationale
    missing = [host for host in hosts if host.lower() not in rationale.lower()]
    if not missing:
        return rationale
    suffix = " Betroffene Hosts vollständig: " + ", ".join(hosts) + "."
    return (rationale.rstrip() + suffix)[:1200]


# ── Public entry point ──────────────────────────────────────────────────────

async def design_dashboard(db: Any, user_id: str) -> dict:
    """Compose a bespoke dashboard for the current situation.

    Returns {"widgets": [{widget_type,title,gs_*,config}], "rationale": str}.
    Always returns a usable set (LLM result or deterministic fallback)."""
    from app.services.ai_language import get_response_language_for_user

    lang = await get_response_language_for_user(db, user_id)
    situation = await gather_situation(db, user_id)

    llm_result = await _ask_llm(db, situation, lang)
    rationale = ""
    specs: list[dict] = []
    used_fallback = False
    if llm_result:
        specs = _validate_widgets(llm_result.get("widgets", []), situation, enforce_budget=False)
        rationale = _clean_rationale(str(llm_result.get("rationale") or ""))

    if not specs:
        used_fallback = True
        specs, fb_rationale = _fallback_widgets(situation, lang)
        if not rationale:
            rationale = fb_rationale
    # NB: the rationale is a SITUATION briefing only — no full host dump
    # (_clean_rationale already strips any trailing "Betroffene Hosts: …").

    # Guarantee the metrics drive the composition: every acute forecast
    # candidate MUST appear as a forecast widget. If the LLM omitted one
    # (it often just mentions it in prose), inject it — this is the whole
    # point of collecting the metrics in the first place.
    specs = _ensure_forecast_widgets(specs, situation, lang)
    specs = _validate_widgets(specs, situation, enforce_budget=True)

    # Expand short host mentions (e.g. "nsa242") in the rationale to their FQDN
    # ("nsa242.example.com"). Qwen3 tends to write short names in prose, which the
    # FQDN-based host-link detection can't match → hosts become unclickable. Using
    # the real situation hosts we restore the full names so the strip stays clickable.
    rationale = _expand_host_mentions(rationale, _situation_hosts(situation))

    degraded = any(state != "available" for state in situation.get("source_state", {}).values())
    placed = _pack_grid(specs)
    return {
        "widgets": placed,
        "rationale": rationale,
        "meta": {
            "version": 1,
            "scope": {"kind": "it_operations", "hosts": situation.get("host_scope", [])},
            "as_of": situation.get("as_of"),
            "source_state": situation.get("source_state", {}),
            "fallback": used_fallback,
            "result_state": "fallback" if used_fallback else ("degraded" if degraded else "generated"),
            "selection_reason": rationale,
            "time_windows": {"event_counts": "1h", "active_problems": "current"},
        },
    }


def _expand_host_mentions(rationale: str, hosts: list[str]) -> str:
    """Replace standalone short-name mentions with the matching FQDN.

    Only expands a short name that unambiguously maps to exactly one known FQDN,
    and never touches a name that is already written as an FQDN."""
    if not rationale or not hosts:
        return rationale
    # short label → FQDN, only when the short label is unique across known hosts.
    short_to_fqdn: dict[str, str] = {}
    seen_twice: set[str] = set()
    for h in hosts:
        short = h.split(".")[0]
        if not short or short == h:
            continue
        if short in short_to_fqdn and short_to_fqdn[short] != h:
            seen_twice.add(short)
        short_to_fqdn.setdefault(short, h)
    # Longest short names first so a prefix never shadows a longer match.
    for short in sorted(short_to_fqdn, key=len, reverse=True):
        if short in seen_twice:
            continue
        fqdn = short_to_fqdn[short]
        # \b<short>\b but NOT already followed by a dot (i.e. not part of an FQDN).
        rationale = _re.sub(rf'\b{_re.escape(short)}\b(?!\.)', fqdn, rationale)
    return rationale


def _ensure_forecast_widgets(specs: list[dict], situation: dict, lang: str) -> list[dict]:
    """Append a forecast widget for any acute candidate the LLM left out."""
    candidates = situation.get("forecast_candidates", [])[:2]
    if not candidates:
        return specs
    present = {
        (s["config"].get("host"), s["config"].get("metric_id"))
        for s in specs if s["widget_type"] == "forecast"
    }
    for c in candidates:
        if (c["host"], c["metric_id"]) in present:
            continue
        specs.append({
            "widget_type": "forecast",
            "title": (
                f"Prognose {c.get('label','')} · {c['host'].split('.')[0]}"
                if lang == "de"
                else f"Forecast {c.get('label','')} · {c['host'].split('.')[0]}"
            ),
            "config": {
                "host": c["host"], "service": c["service"], "metric_id": c["metric_id"],
                "history_hours": 72, "horizon_hours": 24,
            },
        })
    return specs
