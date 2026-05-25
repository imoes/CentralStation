"""CheckMK REST API connector.

Auth: Bearer <user> <password>  (ref: llm-cmk-analyzer/analyzer.py)
API base: supports
  - <base_url>/check_mk/api/1.0
  - <base_url>/<site>/check_mk/api/1.0
  - full API URL directly
"""
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

    async def get_problems(self, time_range_minutes: int = 60) -> list[dict]:
        """Return open WARN/CRIT services, including host tags for filtering."""
        payload = {
            "query": {
                "op": "and",
                "expr": [
                    {"op": "or", "expr": [
                        {"op": "=", "left": "state", "right": "2"},
                        {"op": "=", "left": "state", "right": "1"},
                    ]},
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
