"""CheckMK REST API connector.

Auth: Bearer <user> <password>  (ref: llm-cmk-analyzer/analyzer.py)
API base: <base_url>/check_mk/api/1.0
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class CheckMKConnector(BaseConnector):
    def _headers(self) -> dict:
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        return {
            "Authorization": f"Bearer {user} {password}",
            "Accept": "application/json",
        }

    def _api(self, path: str) -> str:
        return f"{self.base_url}/check_mk/api/1.0{path}"

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(
                    self._api("/domain-types/folder_config/collections/all"),
                    headers=self._headers(),
                    params={"parent": "~"},
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="CheckMK reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

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
                ],
            },
            "columns": [
                "host_name", "description", "state",
                "plugin_output", "acknowledged", "last_state_change",
                "host_tags", "host_labels", "host_address",
            ],
        }
        async with self._client() as client:
            r = await client.post(
                self._api("/domain-types/service/collections/all"),
                headers=self._headers(),
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

            # Extract well-known tag groups (CheckMK standard + ippen.media custom)
            os_val = (
                tags.get("operatingsystem")
                or tags.get("os")
                or labels.get("os", "")
            )
            criticality = (
                tags.get("criticality")
                or labels.get("criticality", "")
            )
            ve = (
                tags.get("virtual")
                or tags.get("ve")
                or tags.get("environment")
                or labels.get("ve", "")
            )
            location = (
                tags.get("networking_segment")
                or tags.get("location")
                or labels.get("location", "")
            )

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
                    "os": os_val,
                    "criticality": criticality,
                    "ve": ve,
                    "location": location,
                    "host_tags": tags,
                    "host_labels": labels,
                },
            })
        return results
