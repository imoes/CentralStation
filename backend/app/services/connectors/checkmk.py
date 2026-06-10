"""CheckMK REST API connector.

Auth: Bearer <user> <password>  (ref: llm-cmk-analyzer/analyzer.py)
API base: supports
  - <base_url>/check_mk/api/1.0
  - <base_url>/<site>/check_mk/api/1.0
  - full API URL directly
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


_OS_LABEL_MAP: dict[str, str] = {
    "os-linux":   "Linux",
    "os-windows": "Windows",
    "os-cisco":   "Cisco",
    "os-snmp":    "SNMP",
    "os-hp-ux":   "HP-UX",
    "os-aix":     "AIX",
    "os-freebsd": "FreeBSD",
    "os-solaris": "Solaris",
    "os-netbsd":  "NetBSD",
    "os-openbsd": "OpenBSD",
    "os-junos":   "JunOS",
    "os-ios":     "Cisco IOS",
    "os-esxi":    "VMware ESXi",
}


def _extract_folder_location(filename: str) -> str:
    """Extract top-level folder from host_filename like /wato/München/sub/.mk → München."""
    if not filename:
        return ""
    # Strip /wato/ prefix and split on /
    stripped = filename.removeprefix("/wato/").removeprefix("wato/")
    parts = [p for p in stripped.split("/") if p and not p.endswith(".mk")]
    return parts[0] if parts else ""


class CheckMKConnector(BaseConnector):
    def _headers(self) -> dict:
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        return {
            "Authorization": f"Bearer {user} {password}",
            "Accept": "application/json",
        }

    def _api_base_candidates(self) -> list[str]:
        base = (self.base_url or "").rstrip("/")
        site = (self.credentials.get("site") or "").strip("/")
        if not base:
            return []

        if base.endswith("/check_mk/api/1.0"):
            return [base]

        parsed = urlparse(base)
        path_parts = [part for part in parsed.path.split("/") if part]
        candidates: list[str] = []

        # Site base already included, e.g. https://host/im
        if path_parts:
            candidates.append(f"{base}/check_mk/api/1.0")
        else:
            if site:
                candidates.append(f"{base}/{site}/check_mk/api/1.0")
            candidates.append(f"{base}/check_mk/api/1.0")

        # Deduplicate while preserving order.
        unique: list[str] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique

    def _api(self, api_base: str, path: str) -> str:
        return f"{api_base}{path}"

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        candidates = self._api_base_candidates()
        if not candidates:
            raise RuntimeError("CheckMK base_url is empty")

        last_response: httpx.Response | None = None
        async with self._client() as client:
            for api_base in candidates:
                response = await client.request(
                    method,
                    self._api(api_base, path),
                    headers=self._headers(),
                    **kwargs,
                )
                if response.status_code != 404:
                    return response
                last_response = response

        if last_response is not None:
            return last_response
        raise RuntimeError("CheckMK request failed before receiving a response")

    async def test_connection(self) -> ConnectorTestResult:
        try:
            r = await self._request(
                "GET",
                "/domain-types/folder_config/collections/all",
                params={"parent": "~"},
            )
            r.raise_for_status()
            return ConnectorTestResult(success=True, message="CheckMK reachable")
        except httpx.HTTPStatusError as e:
            details = {
                "tried_api_bases": self._api_base_candidates(),
                "response_text": e.response.text[:300],
            }
            return ConnectorTestResult(
                success=False,
                message=f"HTTP {e.response.status_code}",
                details=details,
            )
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_all_hosts(self) -> list[dict]:
        """Return all monitored hosts with their tag/label metadata.

        Uses the host_config REST endpoint — returns ALL hosts, not only problem hosts.
        tag_groups can be a list [{id, value:{id}}] or a plain dict, so both are handled.
        """
        try:
            r = await self._request("GET", "/domain-types/host_config/collections/all")
            r.raise_for_status()
        except Exception:
            return []

        results = []
        for item in r.json().get("value", []):
            name = item.get("id", "")
            if not name:
                continue
            ext   = item.get("extensions", {}) or {}
            attrs = ext.get("attributes", {}) or {}

            # tag_groups: list [{id, value:{id}}]  OR  dict {group_id: tag_id}
            tags_raw = attrs.get("tag_groups", {})
            tags: dict = {}
            if isinstance(tags_raw, list):
                for tg in tags_raw:
                    gid = tg.get("id", "")
                    val = tg.get("value", {})
                    tags[gid] = val.get("id", "") if isinstance(val, dict) else str(val or "")
            elif isinstance(tags_raw, dict):
                tags = tags_raw

            labels: dict = attrs.get("labels", {}) or {}

            raw_os = (
                tags.get("tg-os") or tags.get("operatingsystem") or tags.get("os")
                or labels.get("cmk/os_family", "") or labels.get("os", "")
            )
            hostgroups = [k[3:] for k in labels if k.startswith("hg:")]

            results.append({
                "hostname": name,
                "metadata": {
                    "os":          _OS_LABEL_MAP.get(raw_os, raw_os),
                    "criticality": tags.get("criticality") or labels.get("criticality", ""),
                    "ve":          tags.get("tg-ve") or tags.get("ve") or labels.get("ve", ""),
                    "location":    tags.get("tg-location") or tags.get("location") or labels.get("location", ""),
                    "hostgroups":  hostgroups,
                },
            })
        return results

    async def get_problems(self, time_range_minutes: int = 60, include_unknown: bool = False) -> list[dict]:
        """Return open WARN/CRIT services (+ UNKNOWN when include_unknown=True), including host tags for filtering."""
        state_exprs: list[dict] = [
            {"op": "=", "left": "state", "right": "2"},
            {"op": "=", "left": "state", "right": "1"},
        ]
        if include_unknown:
            state_exprs.append({"op": "=", "left": "state", "right": "3"})
        payload = {
            "query": {
                "op": "and",
                "expr": [
                    {"op": "or", "expr": state_exprs},
                    {"op": "=", "left": "acknowledged", "right": "0"},
                    {"op": "=", "left": "scheduled_downtime_depth", "right": "0"},
                    {"op": "=", "left": "host_scheduled_downtime_depth", "right": "0"},
                ],
            },
            "columns": [
                "host_name", "description", "state",
                "plugin_output", "acknowledged", "last_state_change",
                "host_tags", "host_labels", "host_address", "host_filename",
                "host_groups",
            ],
        }
        r = await self._request(
            "POST",
            "/domain-types/service/collections/all",
            json=payload,
        )
        r.raise_for_status()

        state_map = {0: "ok", 1: "warning", 2: "critical", 3: "unknown"}
        results = []
        for item in r.json().get("value", []):
            ext = item.get("extensions", {})
            state = ext.get("state", 2)
            tags: dict = ext.get("host_tags", {}) or {}
            labels: dict = ext.get("host_labels", {}) or {}

            # Extract ippen.media tag groups (keys confirmed from live API)
            # tg-os, tg-ve, tg-location are ippen-specific prefixed tag groups
            raw_os = (
                tags.get("tg-os")
                or tags.get("operatingsystem")
                or tags.get("os")
                or labels.get("cmk/os_family", "")
                or labels.get("os", "")
            )
            os_val = _OS_LABEL_MAP.get(raw_os, raw_os)
            criticality = (
                tags.get("criticality")
                or labels.get("criticality", "")
            )
            ve = (
                tags.get("tg-ve")
                or tags.get("ve")
                or labels.get("ve", "")
            )
            location = (
                tags.get("tg-location")
                or tags.get("location")
                or labels.get("location", "")
            )

            # Site: available in distributed setups; item id is "<site>!<host>!<service>"
            raw_id = item.get("id", "")
            site = raw_id.split("!")[0] if "!" in raw_id else ext.get("site", "")

            # Folder path as fallback if tg-location is empty
            folder_location = _extract_folder_location(ext.get("host_filename", ""))

            # Hostgroups: from host_groups column, supplemented by hg: label prefix
            host_groups_col: list = ext.get("host_groups") or []
            hg_from_labels = [k[3:] for k in labels if k.startswith("hg:")]
            seen: set = set()
            hostgroups: list[str] = []
            for hg in host_groups_col + hg_from_labels:
                if hg and hg not in seen:
                    seen.add(hg)
                    hostgroups.append(hg)

            results.append({
                "source": "checkmk",
                "severity": state_map.get(state, "unknown"),
                "host": ext.get("host_name", ""),
                "service": ext.get("description", ""),
                "output": ext.get("plugin_output", ""),
                "acknowledged": bool(ext.get("acknowledged", 0)),
                "last_state_change": ext.get("last_state_change"),
                "host_address": ext.get("host_address", ""),
                "metadata": {
                    "site": site,
                    "os": os_val,
                    "criticality": criticality,
                    "ve": ve,
                    "location": folder_location or location,
                    "hostgroups": hostgroups,
                    "host_tags": tags,
                    "host_labels": labels,
                },
            })
        return results

    def _basic_auth_headers(self) -> dict:
        """Basic Auth headers for endpoints that don't accept Bearer (e.g. metrics API)."""
        import base64
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}", "Accept": "application/json", "Content-Type": "application/json"}

    async def get_graph_data(
        self,
        host_name: str,
        service_description: str,
        graph_index: int = 0,
        hours: int = 4,
        metric_id: str = "",
    ) -> dict:
        """Fetch RRD time series from CheckMK metrics API.

        Returns {"series": [{"time": iso, "value": float}], "title": str, "unit": str}
        or {"series": [], "error": str} on failure.

        Endpoint: POST /domain-types/metric/actions/get/invoke
        Note: 'site' field must NOT be sent — omitting it makes the API resolve the site
        automatically. Basic Auth required (Bearer is rejected by this endpoint).
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")

        # CheckMK metric endpoint uses graph_index as an implicit ordering;
        # we fetch all metrics and pick by index (default: first = index 0).
        body: dict = {
            "time_range": {"start": fmt(start), "end": fmt(end)},
            "reduce": "max",
            "host_name": host_name,
            "service_description": service_description,
            "type": "single_metric",
        }
        if metric_id:
            body["metric_id"] = metric_id

        # Derive api_base from first candidate (omit /domain-types/... path)
        candidates = self._api_base_candidates()
        if not candidates:
            return {"series": [], "error": "CheckMK base_url is empty"}
        api_base = candidates[0]

        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{api_base}/domain-types/metric/actions/get/invoke",
                    headers=self._basic_auth_headers(),
                    json=body,
                )
        except Exception as e:
            return {"series": [], "error": str(e)}

        if resp.status_code != 200:
            # On failure without metric_id, try fetching the nth metric by listing metrics
            if not metric_id:
                return await self._get_graph_data_by_index(host_name, service_description, graph_index, hours, api_base)
            return {"series": [], "error": f"CheckMK HTTP {resp.status_code}: {resp.text[:200]}"}

        data = resp.json()
        step = int(data.get("step", 60))
        tr = data.get("time_range", {})
        # Parse start timestamp from ISO string returned by API
        from dateutil.parser import parse as parse_dt
        try:
            ts_start = parse_dt(tr["start"]).timestamp()
        except Exception:
            ts_start = start.timestamp()

        metrics: list = data.get("metrics", [])
        if not metrics:
            return {"series": [], "error": "No metrics returned"}

        # Pick by index (graph_index selects which curve)
        curve = metrics[min(graph_index, len(metrics) - 1)]
        title = curve.get("title", service_description)
        data_points: list = curve.get("data_points", [])

        series = [
            {
                "time": datetime.fromtimestamp(ts_start + i * step, tz=timezone.utc).isoformat(),
                "value": round(float(v), 4),
            }
            for i, v in enumerate(data_points)
            if v is not None
        ]
        return {"series": series, "title": title, "unit": ""}

    async def _get_graph_data_by_index(
        self, host_name: str, service_description: str, graph_index: int, hours: int, api_base: str
    ) -> dict:
        """Fetch graph data by listing available metric IDs and picking by index."""
        # Get available metric IDs from service query
        try:
            r = await self._request(
                "POST",
                "/domain-types/service/collections/all",
                json={"query": {"op": "and", "expr": [
                    {"op": "=", "left": "host_name", "right": host_name},
                    {"op": "=", "left": "description", "right": service_description},
                ]}, "columns": ["metrics"]},
            )
            r.raise_for_status()
            items = r.json().get("value", [])
            if not items:
                return {"series": [], "error": f"Service not found: {host_name}/{service_description}"}
            metric_ids: list[str] = items[0].get("extensions", {}).get("metrics", [])
            if not metric_ids:
                return {"series": [], "error": "No metrics available for this service"}
            metric_id = metric_ids[min(graph_index, len(metric_ids) - 1)]
            return await self.get_graph_data(host_name, service_description, graph_index, hours, metric_id=metric_id)
        except Exception as e:
            return {"series": [], "error": str(e)}

    async def get_forecast_data(
        self,
        host_name: str,
        service_description: str,
        metric_id: str = "",
        graph_index: int = 0,
        history_hours: int = 72,
        horizon_hours: int = 24,
    ) -> dict:
        """Return historical RRD data plus a linear-regression forecast.

        CheckMK CEE 2.3 exposes no dedicated forecast REST endpoint — the GUI-only
        Forecast Dashlet is not accessible via API. We therefore fetch extended
        history and project forward using linear regression + ±1-sigma confidence band.

        Returns:
          {
            "series_history":  [{"time": iso, "value": float}, ...],
            "series_forecast":  [{"time": iso, "value": float}, ...],
            "confidence_band":  [{"time": iso, "lower": float, "upper": float}, ...],
            "title": str,
            "unit":  str,
          }
        or {"series_history": [], "error": str} on failure.
        """
        hist = await self.get_graph_data(
            host_name, service_description,
            graph_index=graph_index,
            hours=history_hours,
            metric_id=metric_id,
        )
        if hist.get("error") or not hist.get("series"):
            return {"series_history": [], "series_forecast": [], "confidence_band": [],
                    "error": hist.get("error", "No historical data")}

        series = hist["series"]
        title = hist.get("title", service_description)
        unit = hist.get("unit", "")

        # Convert to numeric timestamps + values for regression
        try:
            from datetime import datetime as _dt, timezone as _tz
            import math

            xs = [_dt.fromisoformat(p["time"]).timestamp() for p in series]
            ys = [p["value"] for p in series]
            n = len(xs)
            if n < 2:
                return {"series_history": series, "series_forecast": [], "confidence_band": [],
                        "title": title, "unit": unit}

            # Linear regression: y = a*x + b
            mx = sum(xs) / n
            my = sum(ys) / n
            ss_xx = sum((x - mx) ** 2 for x in xs)
            ss_xy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            a = ss_xy / ss_xx if ss_xx else 0.0
            b = my - a * mx

            # Residual std-dev for confidence band
            residuals = [y - (a * x + b) for x, y in zip(xs, ys)]
            sigma = math.sqrt(sum(r ** 2 for r in residuals) / n) if n > 1 else 0.0

            # Project forward using same step as history
            step_seconds = (xs[-1] - xs[0]) / (n - 1) if n > 1 else 300
            n_forecast = max(1, int(horizon_hours * 3600 / step_seconds))
            # Cap at 288 points (24h @ 5min step) to keep response size reasonable
            n_forecast = min(n_forecast, 288)

            from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td
            series_forecast = []
            confidence_band = []
            for i in range(1, n_forecast + 1):
                t = xs[-1] + i * step_seconds
                v = a * t + b
                iso = _dt2.fromtimestamp(t, tz=_tz2.utc).isoformat()
                series_forecast.append({"time": iso, "value": round(v, 4)})
                confidence_band.append({
                    "time": iso,
                    "lower": round(v - sigma, 4),
                    "upper": round(v + sigma, 4),
                })

            return {
                "series_history": series,
                "series_forecast": series_forecast,
                "confidence_band": confidence_band,
                "title": title,
                "unit": unit,
            }
        except Exception as e:
            return {"series_history": series, "series_forecast": [], "confidence_band": [],
                    "title": title, "unit": unit, "error": f"Forecast projection failed: {e}"}

    # CheckMK service state int → label
    _STATE_LABELS = {0: "OK", 1: "WARN", 2: "CRIT", 3: "UNKNOWN"}

    async def list_services(self, host_name: str) -> list[dict]:
        """Return all services for a host with name, state, state_label and summary.

        `summary` is the CheckMK plugin_output — the human-readable status line that
        usually carries the current values (e.g. "15.2% used (3.04 GB of 20.0 GB)").
        """
        try:
            r = await self._request(
                "GET",
                "/domain-types/service/collections/all",
                # CheckMK expects `columns` as repeated query params (not comma-joined);
                # httpx emits ?columns=description&columns=state&columns=plugin_output
                params={"host_name": host_name, "columns": ["description", "state", "plugin_output"]},
            )
            r.raise_for_status()
        except Exception:
            return []
        services = []
        for item in r.json().get("value", []):
            ext = item.get("extensions", {})
            name = ext.get("description", "")
            if not name:
                continue
            state = ext.get("state", 0)
            services.append({
                "name": name,
                "state": state,
                "state_label": self._STATE_LABELS.get(state, "UNKNOWN"),
                "summary": ext.get("plugin_output", ""),
            })
        return services
