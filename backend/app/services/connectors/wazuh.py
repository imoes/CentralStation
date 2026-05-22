"""Wazuh REST API connector.

Auth: POST /security/authenticate → JWT → Bearer <token>
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class WazuhConnector(BaseConnector):
    async def _get_token(self, client: httpx.AsyncClient) -> str:
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        r = await client.post(
            f"{self.base_url}/security/authenticate",
            auth=(user, password),
        )
        r.raise_for_status()
        return r.json()["data"]["token"]

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                token = await self._get_token(client)
                r = await client.get(
                    f"{self.base_url}/",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="Wazuh reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_alerts(
        self,
        limit: int = 100,
        min_level: int = 7,
        time_range_minutes: int = 60,
    ) -> list[dict]:
        async with self._client(timeout=30.0) as client:
            token = await self._get_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            r = await client.get(
                f"{self.base_url}/alerts",
                headers=headers,
                params={
                    "limit": limit,
                    "sort": "-timestamp",
                    "q": f"rule.level>={min_level}",
                },
            )
            r.raise_for_status()

        severity_map = {
            range(0, 4): "info",
            range(4, 7): "low",
            range(7, 10): "medium",
            range(10, 13): "high",
            range(13, 16): "critical",
        }

        def to_severity(level: int) -> str:
            for r_, sev in severity_map.items():
                if level in r_:
                    return sev
            return "critical"

        results = []
        for item in r.json().get("data", {}).get("affected_items", []):
            rule = item.get("rule", {})
            level = rule.get("level", 0)
            results.append({
                "source": "wazuh",
                "severity": to_severity(level),
                "title": rule.get("description", ""),
                "body": item.get("full_log", ""),
                "external_id": item.get("id", ""),
                "agent": item.get("agent", {}).get("name", ""),
                "timestamp": item.get("timestamp", ""),
                "rule_id": rule.get("id", ""),
            })
        return results
