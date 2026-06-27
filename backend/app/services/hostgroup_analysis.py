"""CheckMK hostgroup performance pattern analysis (LLM-free stages 1-2, compact stage 3).

Recognises failure/performance patterns across a whole CheckMK hostgroup by
funnelling huge RRD volumes down to a tiny, LLM-ready evidence set:

  Stage 1 — fetch + summarise each (host, metric, window) into compact stats.
  Stage 2 — pure-Python anomaly + correlation detection (short-vs-long deviation,
            cross-metric Pearson, peak-time clusters, fleet z-score) → shortlist.
  Stage 3 — for the shortlist only: fine series around the peak + Graylog logs.

The LLM (in mcp_server / scheduler) only ever sees the Stage-3 output, kept well
under a few thousand tokens. No numpy/scipy — stdlib statistics + math only,
mirroring CheckMKConnector.get_forecast_data.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Native CheckMK RRD windows (hours): 4h / 25h / 8d / 35d.
WINDOWS: list[int] = [4, 25, 192, 840]
CORR_WINDOW = 840          # window whose bucketed series is used for correlation
CORR_BUCKETS = 168         # shared grid resolution for cross-metric correlation
ACUTE_WINDOW = 4           # short window for short-vs-long deviation

# Metric selection rules: (service_substring, metric_id, key_label, unit).
# Only metrics the discovery confirms exist on a host are actually fetched.
_PICK_RULES: list[tuple[str, str, str, str]] = [
    ("CPU load",                 "load1",                 "cpu_load1",     ""),
    ("CPU load",                 "load5",                 "cpu_load5",     ""),
    ("CPU utilization",          "util",                  "cpu_util",      "%"),
    ("Memory",                   "mem_used_percent",      "mem_pct",       "%"),
    ("Filesystem /",             "fs_used_percent",       "fs_root_pct",   "%"),
    ("HTTP response statistics", "avg_response_time",     "http_resp_s",   "s"),
    ("HTTP response statistics", "http_category_500_599", "http_5xx",      ""),
    ("HTTP response statistics", "total_requests",        "http_reqs",     ""),
]


def select_key_metrics(service_metrics: dict[str, list[str]]) -> list[dict]:
    """Pick the curated key metrics that actually exist on a host.

    service_metrics: {service_description: [metric_id, ...]} from discovery.
    Returns [{service, metric_id, key, unit}], deduped by key (first match wins).
    """
    picked: dict[str, dict] = {}
    for svc_sub, metric_id, key, unit in _PICK_RULES:
        if key in picked:
            continue
        for svc, metrics in service_metrics.items():
            if svc_sub in svc and metric_id in metrics:
                picked[key] = {"service": svc, "metric_id": metric_id, "key": key, "unit": unit}
                break
    return list(picked.values())


# ── Stage 1: per-series statistics ───────────────────────────────────────────

def summarize_series(series: list[dict], warn_threshold: float | None = None) -> dict:
    """Compact stats for one RRD series ([{time, value}])."""
    vals = [p["value"] for p in series if p.get("value") is not None]
    if not vals:
        return {"n": 0}
    n = len(vals)
    srt = sorted(vals)
    p95 = srt[min(n - 1, int(round(0.95 * (n - 1))))]
    avg = sum(vals) / n
    stddev = math.sqrt(sum((v - avg) ** 2 for v in vals) / n) if n > 1 else 0.0
    # Peak
    peak_i = max(range(n), key=lambda i: vals[i])
    peak_time = series[peak_i].get("time", "")
    # Trend (first half vs second half)
    mid = n // 2 or 1
    af = sum(vals[:mid]) / mid
    al = sum(vals[mid:]) / max(len(vals[mid:]), 1)
    trend = "rising" if al > af * 1.07 else "falling" if al < af * 0.93 else "stable"
    # Slope per hour via linear regression on time
    slope_per_hour = 0.0
    try:
        ts = [datetime.fromisoformat(p["time"]).timestamp() for p in series if p.get("value") is not None]
        if len(ts) > 1:
            mx = sum(ts) / len(ts); my = avg
            ss_xx = sum((x - mx) ** 2 for x in ts)
            ss_xy = sum((x - mx) * (y - my) for x, y in zip(ts, vals))
            slope_per_hour = (ss_xy / ss_xx * 3600) if ss_xx else 0.0
    except Exception:
        pass
    breach = sum(1 for v in vals if warn_threshold is not None and v >= warn_threshold)
    return {
        "n": n,
        "min": round(min(vals), 3), "max": round(max(vals), 3),
        "avg": round(avg, 3), "p95": round(p95, 3), "stddev": round(stddev, 3),
        "trend": trend, "slope_per_hour": round(slope_per_hour, 5),
        "peak_value": round(vals[peak_i], 3), "peak_time": peak_time,
        "breach_count": breach,
    }


async def collect_hostgroup_stats(
    connector, hosts: list[str], host_metrics: dict[str, list[dict]],
    windows: list[int] | None = None, max_concurrency: int = 8,
) -> dict[str, dict[str, dict]]:
    """Fetch + summarise every (host, key-metric, window).

    host_metrics: {host: [{service, metric_id, key, unit}, ...]} from select_key_metrics.
    Returns {host: {key: {"meta":{service,metric_id,unit}, "windows":{h:stat},
             "corr_series":[floats]}}}. Failed graphs are skipped silently.
    """
    windows = windows or WINDOWS
    sem = asyncio.Semaphore(max_concurrency)
    out: dict[str, dict[str, dict]] = {}

    async def _one(host: str, m: dict):
        key, svc, mid, unit = m["key"], m["service"], m["metric_id"], m["unit"]
        rec: dict = {"meta": {"service": svc, "metric_id": mid, "unit": unit},
                     "windows": {}, "corr_series": []}
        for w in windows:
            async with sem:
                data = await connector.get_metric_series_bucketed(
                    host, svc, metric_id=mid, hours=w,
                    buckets=CORR_BUCKETS if w == CORR_WINDOW else max(48, w // 2),
                )
            series = data.get("series", [])
            if not series:
                continue
            rec["windows"][w] = summarize_series(series)
            if w == CORR_WINDOW:
                rec["corr_series"] = [p["value"] for p in series if p.get("value") is not None]
        if rec["windows"]:
            out.setdefault(host, {})[key] = rec

    tasks = [_one(h, m) for h in hosts for m in host_metrics.get(h, [])]
    await asyncio.gather(*tasks, return_exceptions=True)
    return out


# ── Stage 2: anomaly + correlation (pure Python) ─────────────────────────────

def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r on aligned values (>=8 points, nonzero variance)."""
    n = min(len(xs), len(ys))
    if n < 8:
        return None
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


# Tautological metric pairs whose correlation carries no diagnostic signal
# (load1/load5 are the same load at different averaging windows).
_TRIVIAL_PAIRS: set[frozenset] = {frozenset({"cpu_load1", "cpu_load5"})}


def cross_metric_correlations(stats: dict, min_abs_r: float = 0.6) -> list[dict]:
    """Per host, Pearson between every key-metric pair on the shared corr grid.

    Skips tautological pairs (e.g. load1/load5) so meaningful cross-domain
    correlations (CPU ↔ HTTP response time ↔ 5xx) surface instead of being
    crowded out.
    """
    out: list[dict] = []
    for host, metrics in stats.items():
        keys = [k for k, r in metrics.items() if len(r.get("corr_series", [])) >= 8]
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                if frozenset({a, b}) in _TRIVIAL_PAIRS:
                    continue
                r = pearson(metrics[a]["corr_series"], metrics[b]["corr_series"])
                if r is not None and abs(r) >= min_abs_r:
                    out.append({"host": host, "metric_a": a, "metric_b": b,
                                "r": round(r, 3),
                                "n": min(len(metrics[a]["corr_series"]), len(metrics[b]["corr_series"]))})
    out.sort(key=lambda x: abs(x["r"]), reverse=True)
    return out


def short_vs_long_deviation(stats: dict, ratio: float = 1.5) -> list[dict]:
    """Flag host/metric where the acute (4h) p95 spikes above the long (35d) baseline."""
    out: list[dict] = []
    for host, metrics in stats.items():
        for key, rec in metrics.items():
            w = rec["windows"]
            acute, base = w.get(ACUTE_WINDOW), w.get(CORR_WINDOW)
            if not acute or not base:
                continue
            bp95 = base.get("p95", 0) or 0
            ap95 = acute.get("p95", 0) or 0
            if bp95 > 0 and ap95 >= bp95 * ratio:
                out.append({"host": host, "metric": key,
                            "acute_p95": ap95, "baseline_p95": bp95,
                            "deviation": round(ap95 / bp95, 2)})
    out.sort(key=lambda x: x["deviation"], reverse=True)
    return out


def fleet_zscores(stats: dict, metric_key: str) -> list[dict]:
    """Z-score of each host's 35d p95 for one metric vs the rest of the fleet."""
    pairs = [(h, m[metric_key]["windows"].get(CORR_WINDOW, {}).get("p95"))
             for h, m in stats.items() if metric_key in m]
    pairs = [(h, v) for h, v in pairs if v is not None]
    if len(pairs) < 4:
        return []
    vals = [v for _, v in pairs]
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    if sd == 0:
        return []
    return sorted(
        [{"host": h, "metric": metric_key, "p95": v, "z": round((v - mean) / sd, 2)}
         for h, v in pairs if abs((v - mean) / sd) >= 2.0],
        key=lambda x: abs(x["z"]), reverse=True,
    )


def peak_time_clusters(stats: dict, metric_key: str, window_minutes: int = 120) -> list[dict]:
    """Cluster hosts whose 35d peak for a metric falls within window_minutes."""
    peaks: list[tuple[str, float]] = []
    for host, metrics in stats.items():
        rec = metrics.get(metric_key)
        if not rec:
            continue
        pt = rec["windows"].get(CORR_WINDOW, {}).get("peak_time")
        if not pt:
            continue
        try:
            peaks.append((host, datetime.fromisoformat(pt).timestamp()))
        except Exception:
            continue
    peaks.sort(key=lambda x: x[1])
    clusters: list[dict] = []
    win = window_minutes * 60
    used: set[int] = set()
    for i, (h, t) in enumerate(peaks):
        if i in used:
            continue
        group = [(h, t)]
        used.add(i)
        for j in range(i + 1, len(peaks)):
            if j in used:
                continue
            if abs(peaks[j][1] - t) <= win:
                group.append(peaks[j]); used.add(j)
        if len(group) >= 3:
            clusters.append({
                "metric": metric_key,
                "bucket_start": datetime.fromtimestamp(group[0][1], tz=timezone.utc).isoformat(),
                "count": len(group),
                "hosts": [g[0] for g in group][:20],
            })
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def anomaly_shortlist(stats: dict, deviations: list[dict], correlations: list[dict],
                      top_n: int = 12) -> list[dict]:
    """Rank host/metric records to escalate to Stage 3, with the 'why' attached."""
    scored: dict[tuple[str, str], dict] = {}

    def _bump(host, metric, reason, weight):
        k = (host, metric)
        rec = scored.setdefault(k, {"host": host, "metric": metric, "score": 0.0, "reasons": []})
        rec["score"] += weight
        rec["reasons"].append(reason)

    for d in deviations:
        _bump(d["host"], d["metric"], f"acute spike ×{d['deviation']} vs 35d baseline", 2.0 * d["deviation"])
    for c in correlations[:30]:
        _bump(c["host"], c["metric_a"], f"correlates with {c['metric_b']} (r={c['r']})", 1.0 + abs(c["r"]))
        _bump(c["host"], c["metric_b"], f"correlates with {c['metric_a']} (r={c['r']})", 1.0 + abs(c["r"]))
    # fleet z-scores per metric present
    metric_keys = {k for m in stats.values() for k in m}
    for mk in metric_keys:
        for z in fleet_zscores(stats, mk):
            _bump(z["host"], z["metric"], f"fleet-outlier z={z['z']} (p95={z['p95']})", 1.5 * abs(z["z"]))

    ranked = sorted(scored.values(), key=lambda r: r["score"], reverse=True)[:top_n]
    # attach peak_time/value from stats for Stage 3
    for r in ranked:
        rec = stats.get(r["host"], {}).get(r["metric"], {})
        w = rec.get("windows", {}).get(CORR_WINDOW, {})
        r["peak_time"] = w.get("peak_time", "")
        r["peak_value"] = w.get("peak_value")
        r["service"] = rec.get("meta", {}).get("service", "")
        r["metric_id"] = rec.get("meta", {}).get("metric_id", "")
        r["reasons"] = r["reasons"][:4]
        r["score"] = round(r["score"], 2)
    return ranked


# ── Stage 3: drilldown + Graylog log correlation ─────────────────────────────

async def drilldown_with_logs(
    connector, shortlist: list[dict], db=None,
    log_window_minutes: int = 120, max_log_lines: int = 8,
) -> list[dict]:
    """For each shortlisted host/metric, fetch Graylog lines around the peak.

    Graylog search only supports 'last N seconds', so we widen since_seconds to
    cover from the peak until now and post-filter by timestamp.
    """
    from app.services.feed_index import search_by_query
    out: list[dict] = []
    now = datetime.now(timezone.utc)
    seen_hosts: dict[str, list] = {}

    for item in shortlist:
        host = item["host"]
        rec = {**item, "log_sample": []}
        if host not in seen_hosts:
            since = 14 * 24 * 3600  # 14 days back, post-filtered below
            try:
                logs = await search_by_query(
                    index_pattern="cs-feed-graylog",
                    query_string=f'metadata.host:"{host}"',
                    size=30, since_seconds=since, db=db,
                )
            except Exception as e:
                log.debug("graylog lookup failed for %s: %s", host, e)
                logs = []
            seen_hosts[host] = logs
        logs = seen_hosts[host]

        # Post-filter to the peak window when we have a peak_time
        peak_ts = None
        try:
            if item.get("peak_time"):
                peak_ts = datetime.fromisoformat(item["peak_time"]).timestamp()
        except Exception:
            peak_ts = None
        win = log_window_minutes * 60
        sample: list[str] = []
        for d in logs:
            txt = (d.get("title") or d.get("body") or "").strip().replace("\n", " ")[:160]
            if not txt:
                continue
            if peak_ts is not None:
                try:
                    lt = datetime.fromisoformat((d.get("created_at") or "").replace("Z", "+00:00")).timestamp()
                    if abs(lt - peak_ts) > win:
                        continue
                except Exception:
                    pass
            sample.append(txt)
            if len(sample) >= max_log_lines:
                break
        rec["log_sample"] = sample
        out.append(rec)
    return out


# ── Orchestration ────────────────────────────────────────────────────────────

# Module-level cache: {(group, windows_tuple, correlate_logs): (epoch, bundle)}.
# Stage 1 fetches ~2300 RRD series (~150s); interactive callers reuse a recent
# snapshot instead of re-scanning. The 6h scheduler always passes fresh=True.
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL = 1800  # 30 minutes


async def analyze_hostgroup(
    connector, group_name: str, db=None, correlate_logs: bool = True,
    windows: list[int] | None = None, top_n: int = 12, fresh: bool = False,
) -> dict:
    """Run stages 1-3 and return the compact analysis bundle (no LLM).

    Cached for 30 min keyed by (group, windows, correlate_logs) unless fresh=True.
    """
    import time as _time
    cache_key = (group_name, tuple(windows or WINDOWS), correlate_logs)
    if not fresh:
        hit = _CACHE.get(cache_key)
        if hit and (_time.time() - hit[0]) < _CACHE_TTL:
            return {**hit[1], "cached": True}

    bundle = await _analyze_hostgroup_uncached(
        connector, group_name, db=db, correlate_logs=correlate_logs,
        windows=windows, top_n=top_n,
    )
    if "error" not in bundle:
        _CACHE[cache_key] = (_time.time(), bundle)
    return bundle


async def _analyze_hostgroup_uncached(
    connector, group_name: str, db=None, correlate_logs: bool = True,
    windows: list[int] | None = None, top_n: int = 12,
) -> dict:
    hosts = await connector.get_hosts_in_group(group_name)
    if not hosts:
        return {"group_name": group_name, "error": "Hostgruppe nicht gefunden oder leer", "hosts": 0}

    group_metrics = await connector.discover_group_service_metrics(group_name)
    host_metrics = {h: select_key_metrics(group_metrics.get(h, {})) for h in hosts}

    stats = await collect_hostgroup_stats(connector, hosts, host_metrics, windows=windows)

    deviations = short_vs_long_deviation(stats)
    correlations = cross_metric_correlations(stats)
    shortlist = anomaly_shortlist(stats, deviations, correlations, top_n=top_n)

    # Peak clusters for the metrics that appear in the shortlist
    cluster_metrics = {s["metric"] for s in shortlist}
    clusters: list[dict] = []
    for mk in cluster_metrics:
        clusters.extend(peak_time_clusters(stats, mk))
    clusters.sort(key=lambda c: c["count"], reverse=True)

    if correlate_logs and shortlist:
        shortlist = await drilldown_with_logs(connector, shortlist, db=db)

    # Fleet aggregates: avg/p95 per metric across the group (35d window)
    fleet: dict[str, dict] = {}
    metric_keys = {k for m in stats.values() for k in m}
    for mk in metric_keys:
        p95s = [m[mk]["windows"].get(CORR_WINDOW, {}).get("p95")
                for m in stats.values() if mk in m]
        p95s = [v for v in p95s if v is not None]
        if p95s:
            fleet[mk] = {"hosts": len(p95s),
                         "fleet_avg_p95": round(sum(p95s) / len(p95s), 3),
                         "fleet_max_p95": round(max(p95s), 3)}

    return {
        "group_name": group_name,
        "hosts": len(hosts),
        "hosts_with_metrics": len(stats),
        "windows": windows or WINDOWS,
        "fleet_aggregates": fleet,
        "deviations": deviations[:10],
        "correlations": correlations[:15],
        "peak_clusters": clusters[:8],
        "shortlist": shortlist,
    }


async def name_patterns(bundle: dict, llm_config) -> dict:
    """Hand the compact bundle to the LLM to name patterns.

    Returns {"severity_summary": str, "patterns": [...]} or
    {"patterns": [], "error": str} / {"patterns": [], "note": str}.
    """
    import json as _json
    from app.services.llm_client import generate_text
    from app.services.ai_agent.prompts import HOSTGROUP_PATTERN_SYSTEM

    if not getattr(llm_config, "is_configured", False):
        return {"patterns": [], "severity_summary": "none",
                "note": "Kein LLM konfiguriert — nur Rohbefunde."}

    user_msg = build_llm_user_message(bundle)
    try:
        raw = (await generate_text(
            llm_config,
            [{"role": "system", "content": HOSTGROUP_PATTERN_SYSTEM},
             {"role": "user", "content": user_msg}],
            temperature=0.1, reasoning_effort="medium",
        )).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = _json.loads(raw)
    except Exception as exc:
        return {"patterns": [], "severity_summary": "none",
                "error": f"LLM-Musteranalyse fehlgeschlagen: {exc}"}
    return {"severity_summary": parsed.get("severity_summary", "none"),
            "patterns": parsed.get("patterns", [])}


def build_llm_user_message(bundle: dict) -> str:
    """Render the compact analysis bundle into a deterministic LLM user message."""
    g = bundle.get("group_name", "?")
    lines = [f"Hostgruppe: {g} ({bundle.get('hosts',0)} Hosts, {bundle.get('hosts_with_metrics',0)} mit Metriken)",
             f"Fenster: {bundle.get('windows')} Stunden (4h/25h/8d/35d)", ""]

    fa = bundle.get("fleet_aggregates", {})
    if fa:
        lines.append("Fleet-Aggregate (35d p95):")
        for mk, v in fa.items():
            lines.append(f"  {mk}: avg_p95={v['fleet_avg_p95']} max_p95={v['fleet_max_p95']} ({v['hosts']} Hosts)")
        lines.append("")

    dev = bundle.get("deviations", [])
    if dev:
        lines.append("Akute Abweichungen (4h-p95 ggü. 35d-Baseline):")
        for d in dev:
            lines.append(f"  {d['host']} {d['metric']}: ×{d['deviation']} (akut {d['acute_p95']} / Basis {d['baseline_p95']})")
        lines.append("")

    cor = bundle.get("correlations", [])
    if cor:
        lines.append("Cross-Metrik-Korrelationen (|r|>=0.6):")
        for c in cor:
            lines.append(f"  {c['host']}: {c['metric_a']} ↔ {c['metric_b']} r={c['r']} (n={c['n']})")
        lines.append("")

    cl = bundle.get("peak_clusters", [])
    if cl:
        lines.append("Peak-Zeit-Cluster (gleichzeitig spitzende Hosts):")
        for c in cl:
            lines.append(f"  {c['metric']} @ {c['bucket_start']}: {c['count']} Hosts ({', '.join(c['hosts'][:8])})")
        lines.append("")

    sl = bundle.get("shortlist", [])
    if sl:
        lines.append("Anomalie-Shortlist (Top, mit Belegen):")
        for s in sl:
            lines.append(f"  {s['host']} {s['metric']} (score {s.get('score')}): {'; '.join(s.get('reasons', []))}")
            for ln in s.get("log_sample", [])[:6]:
                lines.append(f"      log: {ln}")
        lines.append("")

    return "\n".join(lines)
