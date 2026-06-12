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

    async def get_vm_host(self, vm_name: str) -> str | None:
        """Return the physical host (device name) that runs the given VM."""
        async with self._client(timeout=20.0) as client:
            r = await client.get(
                self._api("/virtualization/virtual-machines/"),
                headers=self._headers(),
                params={"name": vm_name},
            )
            r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        device = results[0].get("device")
        return device.get("name") if device else None

    async def get_cluster_hosts(self, cluster_name: str) -> list[str]:
        """Return hostnames of all physical devices in a cluster."""
        async with self._client(timeout=20.0) as client:
            r = await client.get(
                self._api("/dcim/devices/"),
                headers=self._headers(),
                params={"cluster": cluster_name},
            )
            r.raise_for_status()
        return [d.get("name", "") for d in r.json().get("results", []) if d.get("name")]

    async def get_device_site(self, device_name: str) -> str | None:
        """Return the site/location name for a physical device."""
        device = await self.get_device(device_name)
        if not device:
            return None
        site = device.get("site")
        return site.get("name") if site else None

    async def _get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages of a NetBox list endpoint (follows `next` links)."""
        out: list[dict] = []
        url = self._api(path)
        p: dict | None = {"limit": 500, **(params or {})}
        async with self._client(timeout=60.0) as client:
            while url:
                r = await client.get(url, headers=self._headers(), params=p)
                r.raise_for_status()
                data = r.json()
                out.extend(data.get("results", []))
                url = data.get("next")
                p = None  # next-URL already contains query params
        return out

    async def get_all_devices(self) -> list[dict]:
        """All physical devices with site, cluster, role, status."""
        return await self._get_all("/dcim/devices/")

    async def get_all_vms(self) -> list[dict]:
        """All VMs with device, cluster, status."""
        return await self._get_all("/virtualization/virtual-machines/")
