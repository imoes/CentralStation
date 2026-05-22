"""Prometheus HTTP API connector.

Auth: optional Basic Auth or Bearer Token.
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class PrometheusConnector(BaseConnector):
    def _auth(self) -> tuple[str, str] | None:
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        return (user, password) if user else None

    def _bearer_headers(self) -> dict:
        token = self.credentials.get("token", "")
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}/api/v1/status/buildinfo",
                    auth=self._auth(),
                    headers=self._bearer_headers(),
                )
                r.raise_for_status()
            data = r.json().get("data", {})
            return ConnectorTestResult(
                success=True,
                message=f"Prometheus {data.get('version', 'OK')}",
            )
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def query(self, promql: str) -> dict:
        async with self._client(timeout=30.0) as client:
            r = await client.get(
                f"{self.base_url}/api/v1/query",
                auth=self._auth(),
                headers=self._bearer_headers(),
                params={"query": promql},
            )
            r.raise_for_status()
        return r.json()

    async def query_range(
        self, promql: str, start: str, end: str, step: str = "60s"
    ) -> dict:
        async with self._client(timeout=30.0) as client:
            r = await client.get(
                f"{self.base_url}/api/v1/query_range",
                auth=self._auth(),
                headers=self._bearer_headers(),
                params={"query": promql, "start": start, "end": end, "step": step},
            )
            r.raise_for_status()
        return r.json()
