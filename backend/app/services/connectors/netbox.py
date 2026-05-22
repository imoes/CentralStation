"""NetBox REST API connector.

Auth: Bearer Token — Authorization: Token <api_token>
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class NetBoxConnector(BaseConnector):
    def _headers(self) -> dict:
        token = self.credentials.get("token", "")
        return {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        }

    def _api(self, path: str) -> str:
        return f"{self.base_url}/api{path}"

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(
                    self._api("/status/"),
                    headers=self._headers(),
                )
                r.raise_for_status()
            data = r.json()
            return ConnectorTestResult(
                success=True,
                message=f"NetBox {data.get('netbox-version', 'OK')}",
            )
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def find_prefix_by_ip(self, ip: str) -> dict | None:
        """Return the most-specific prefix containing the given IP."""
        async with self._client() as client:
            r = await client.get(
                self._api("/ipam/prefixes/"),
                headers=self._headers(),
                params={"contains": ip},
            )
            r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        # Prefer longest prefix (most specific)
        return max(results, key=lambda p: int(p["prefix"].split("/")[1]))

    async def get_device(self, name: str) -> dict | None:
        async with self._client() as client:
            r = await client.get(
                self._api("/dcim/devices/"),
                headers=self._headers(),
                params={"name": name},
            )
            r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None

    async def get_vms(self, cluster_name: str | None = None) -> list[dict]:
        params: dict = {}
        if cluster_name:
            params["cluster"] = cluster_name
        async with self._client(timeout=30.0) as client:
            r = await client.get(
                self._api("/virtualization/virtual-machines/"),
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
        return r.json().get("results", [])
