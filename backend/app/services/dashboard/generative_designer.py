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
from typing import Any

log = logging.getLogger(__name__)

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
    "ai_summary": {"agent_type"},
    "war_room":   {"agent_type"},
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
    "ai_summary": (6, 4),
    "war_room":   (12, 5),
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


async def _get_scoped_severity_counts(os_client: Any, host_scope: list[str]) -> dict[str, int]:
    """Severity counts constrained to the user's selected CheckMK host scope."""
    from datetime import datetime, timezone, timedelta

    hosts = [h for h in host_scope if h]
    if not hosts:
        return {}
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
        return {}


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

    os_client = get_opensearch()
    host_scope = await get_user_checkmk_host_scope(db, user_id) if user_id else []
    host_scope_lc = {h.lower() for h in host_scope if h}

    # Severity counts (last hour, scoped to the user's selected sites when set)
    sev_counts = await _get_scoped_severity_counts(os_client, host_scope) if host_scope else await _get_severity_counts()

    # Latest sysadmin analysis findings
    findings: list[dict] = []
    severity_summary = "none"
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
    except Exception as e:
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
        log.debug("gather_situation: metrics failed: %s", e)

    # Worklist top items
    worklist_items: list[dict] = []
    try:
        wl = await get_latest_worklist(db)
        if wl:
            for item in (wl.get("items") or [])[:6]:
                if host_scope_lc and (item.get("host") or "").lower() not in host_scope_lc:
                    continue
                worklist_items.append({
                    "host": item.get("host", ""),
                    "title": item.get("title", ""),
                    "severity": item.get("severity", ""),
                    "source": item.get("source", ""),
                })
    except Exception as e:
        log.debug("gather_situation: worklist failed: %s", e)

    return {
        "severity_counts": sev_counts,
        "severity_summary": severity_summary,
        "host_scope": host_scope,
        "findings": findings,
        "vitals": vitals,
        "forecast_candidates": forecast_candidates,
        "worklist": worklist_items,
    }


# ── 2. LLM composition ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Du bist Dashboard-Designer für ein IT-Operations-Cockpit (CentralStation).
Komponiere ein maßgeschneidertes Dashboard, das die AKTUELLE Lage optimal abbildet.
Wähle 4–8 Widgets, ihre Anordnung in einem 12-Spalten-Grid und ihre Konfiguration.

NUTZE NUR diese Widget-Typen mit exakt diesen config-Schlüsseln:
- "stat": {"index_pattern":"cs-feed-*","query_string":"<lucene>"}  — eine große Kennzahl
- "list": {"index_pattern":"cs-feed-*","query_string":"<lucene>","limit":15}  — Alert-Liste
- "donut": {"index_pattern":"cs-feed-*","query_string":"<lucene>"}  — Severity-Verteilung
- "bar": {"index_pattern":"cs-feed-*","query_string":"<lucene>","agg_field":"source","limit":10}  — agg_field MUSS einer dieser Werte sein: "source", "severity", "host" (kein "|", kein zusammengesetzter Wert)
- "top_hosts": {"index_pattern":"cs-feed-*","query_string":"<lucene>","limit":8}  — Problem-Hosts
- "ai_summary": {"agent_type":"sysadmin"}  — KI-Lagebericht
- "war_room": {"agent_type":"sysadmin"}  — Blast-Radius (NUR bei critical/high sinnvoll)
- "timeseries": {"data_source":"checkmk","host":"<host>","service":"<service>","metric_id":"<metric_id>","hours":4}  — Metrik-Verlauf
- "forecast": {"host":"<host>","service":"<service>","metric_id":"<metric_id>","history_hours":72,"horizon_hours":24}

REGELN:
- Lucene query_string: z.B. "severity:critical AND NOT status:resolved". Standard immer "NOT status:resolved" anhängen.
- WICHTIG: Wenn in forecast_candidates ein Host auf einen Schwellwert zuläuft, MUSST du ein "forecast"-Widget
  mit EXAKT dessen host/service/metric_id einfügen — kopiere diese Werte unverändert. Erfinde keine metric_ids.
- Für "timeseries" nur Einträge aus der vitals-Liste verwenden — kopiere host, service UND metric_id unverändert.
- Bei hohem Ressourcendruck (vitals) ein timeseries-Widget für den Druck-Host.
- Bei critical/high: ai_summary und/oder war_room prominent oben platzieren.
- Bei ruhiger Lage: kompakteres Dashboard (Counts + Liste + Donut), keine Forecasts erzwingen.
- Gruppiere thematisch; nutze die volle Breite (gs_w summiert sich pro Zeile zu 12).

Die "rationale" ist ein LAGE-BRIEFING für den Sysadmin — beschreibe präzise was los ist.
Pflichtinhalt (sofern Daten vorhanden):
- Konkrete Severity/Anzahl der offenen Alerts (z.B. "2 critical, 5 high")
- Namentlich die 2-4 wichtigsten betroffenen Hosts mit dem jeweiligen Problem
- Quantitative Metrik-Werte: RAM/Disk-Auslastung in % + ETA bis Schwellwert für Forecast-Kandidaten
- CPU-Last auf auffälligen Hosts falls vorhanden
NICHT beschreiben, welche Widgets gewählt wurden oder wie das Layout aussieht. KEINE Widget-Typnamen.
Maximal 4 präzise Sätze. Lieber kürzer als ausschweifend — nur die dringenden Facts.

Antworte AUSSCHLIESSLICH mit JSON in genau dieser Form (keine Erklärung außerhalb):
{"rationale":"<2-3 Sätze Lage-Briefing: was ist los, was ist kritisch, worauf achten>",
 "widgets":[{"type":"...","title":"...","gs_x":0,"gs_y":0,"gs_w":4,"gs_h":3,"config":{...}}]}"""


async def _ask_llm(db: Any, situation: dict) -> dict | None:
    """One LLM call → parsed {rationale, widgets}. Returns None on any failure."""
    from app.services.settings import get_llm_config
    from app.services.llm_client import generate_text, LLMInvocationError

    llm_cfg = await get_llm_config(db)
    if not llm_cfg.is_configured:
        log.info("generative_designer: no LLM configured → fallback")
        return None

    user_msg = "Aktuelle Lage:\n" + json.dumps(situation, ensure_ascii=False, indent=0)
    try:
        raw = await generate_text(
            llm_cfg,
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            reasoning_effort="low",
            max_output_tokens=2000,
        )
    except LLMInvocationError as e:
        log.warning("generative_designer: LLM call failed: %s", e)
        return None
    except Exception as e:
        log.warning("generative_designer: LLM error: %s", e)
        return None

    parsed = _parse_json(raw)
    if not parsed or not isinstance(parsed.get("widgets"), list):
        log.warning("generative_designer: LLM returned no usable widgets")
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
    """Tolerant JSON extraction — strips code fences and finds the object."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # remove ```json ... ``` fences
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    # Find the outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


# ── 3. Validation + grid packing ────────────────────────────────────────────

def _validate_widgets(raw_widgets: list, situation: dict) -> list[dict]:
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
        wtype = str(w.get("type", "")).strip()
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
        elif wtype in ("ai_summary", "war_room"):
            cfg["agent_type"] = cfg.get("agent_type") or "sysadmin"
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

    return clean[:8]


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

def _fallback_widgets(situation: dict) -> tuple[list[dict], str]:
    """Deterministic dashboard when the LLM is unavailable / unusable.

    Always includes the acute forecast widgets so the metrics still drive the
    composition even without an LLM."""
    specs: list[dict] = [
        {"widget_type": "stat", "title": "Kritisch",
         "config": {"index_pattern": "cs-feed-*", "query_string": "severity:critical AND NOT status:resolved"}},
        {"widget_type": "stat", "title": "Hoch",
         "config": {"index_pattern": "cs-feed-*", "query_string": "severity:high AND NOT status:resolved"}},
        {"widget_type": "stat", "title": "Gesamt",
         "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved"}},
        {"widget_type": "ai_summary", "title": "KI-Lagebericht",
         "config": {"agent_type": "sysadmin"}},
        {"widget_type": "list", "title": "Aktive Alerts",
         "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved", "limit": 15}},
        {"widget_type": "top_hosts", "title": "Top Problem-Hosts",
         "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved", "limit": 8}},
    ]
    sev = situation.get("severity_summary", "none")
    if sev in ("critical", "high"):
        specs.insert(3, {"widget_type": "war_room", "title": "War Room",
                         "config": {"agent_type": "sysadmin"}})
    # The acute forecast candidates → one forecast widget each (max 2)
    for c in situation.get("forecast_candidates", [])[:2]:
        specs.append({
            "widget_type": "forecast",
            "title": f"Prognose {c.get('label','')} · {c['host'].split('.')[0]}",
            "config": {
                "host": c["host"], "service": c["service"], "metric_id": c["metric_id"],
                "history_hours": 72, "horizon_hours": 24,
            },
        })
    n_fc = len(situation.get("forecast_candidates", [])[:2])
    if n_fc:
        host_list = ", ".join(c["host"] for c in situation.get("forecast_candidates", [])[:2])
        rationale = (f"Automatisches Lagebild: {sev}-Lage, {n_fc} Host(s) laufen laut "
                     f"Metrik-Prognose auf einen Schwellwert zu ({host_list}) — Prognose-Widgets ergänzt.")
    else:
        rationale = f"Automatisches Lagebild ({sev}-Lage) — KI-Komposition nicht verfügbar, Standardauswahl."
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
    situation = await gather_situation(db, user_id)

    llm_result = await _ask_llm(db, situation)
    rationale = ""
    specs: list[dict] = []
    if llm_result:
        specs = _validate_widgets(llm_result.get("widgets", []), situation)
        rationale = _clean_rationale(str(llm_result.get("rationale") or ""))

    if not specs:
        specs, fb_rationale = _fallback_widgets(situation)
        if not rationale:
            rationale = fb_rationale
    # NB: the rationale is a SITUATION briefing only — no full host dump
    # (_clean_rationale already strips any trailing "Betroffene Hosts: …").

    # Guarantee the metrics drive the composition: every acute forecast
    # candidate MUST appear as a forecast widget. If the LLM omitted one
    # (it often just mentions it in prose), inject it — this is the whole
    # point of collecting the metrics in the first place.
    specs = _ensure_forecast_widgets(specs, situation)

    placed = _pack_grid(specs)
    return {"widgets": placed, "rationale": rationale}


def _ensure_forecast_widgets(specs: list[dict], situation: dict) -> list[dict]:
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
            "title": f"Prognose {c.get('label','')} · {c['host'].split('.')[0]}",
            "config": {
                "host": c["host"], "service": c["service"], "metric_id": c["metric_id"],
                "history_hours": 72, "horizon_hours": 24,
            },
        })
    return specs[:9]  # allow one extra slot beyond the 8-widget soft cap
