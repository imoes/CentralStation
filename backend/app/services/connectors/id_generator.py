"""ID-Generator connector.

Auth: Basic Auth — idgen_reader:ippenmedia (read-only)
Used to resolve:
  - IP → location (via ip_networks + locations)
  - Switch name → location_id → location name/city
  - NSA/NSS/NSC device lists

Credential defaults are stored in DB; the idgen_reader account is read-only.
"""
import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class IDGeneratorConnector(BaseConnector):
    def _auth(self) -> tuple[str, str]:
        user = self.credentials.get("username", "idgen_reader")
        password = self.credentials.get("password", "ippenmedia")
        return (user, password)

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}/locations",
                    auth=self._auth(),
                    params={"limit": 1},
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="ID-Generator reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_locations(self) -> list[dict]:
        async with self._client(timeout=30.0) as client:
            r = await client.get(
                f"{self.base_url}/locations",
                auth=self._auth(),
            )
            r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else r.json().get("results", [])

    async def resolve_ip_to_location(self, ip: str) -> dict | None:
        """Resolve an IP address to a location via ID-Generator."""
        async with self._client() as client:
            r = await client.get(
                f"{self.base_url}/ip-to-location",
                auth=self._auth(),
                params={"ip": ip},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
        return r.json()

    async def get_switch_devices(self, switch_type: str) -> list[dict]:
        """switch_type: 'nsa' | 'nss' | 'nsc'"""
        endpoint_map = {
            "nsa": "switchnsadevices",
            "nss": "switchnssdevices",
            "nsc": "switchnscdevices",
        }
        endpoint = endpoint_map.get(switch_type.lower(), "switchnsadevices")
        async with self._client(timeout=30.0) as client:
            r = await client.get(
                f"{self.base_url}/{endpoint}",
                auth=self._auth(),
            )
            r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else r.json().get("results", [])

    async def resolve_switch_to_location(self, switch_name: str) -> dict | None:
        """Given a switch name (e.g. NSA001), return location data."""
        switch_type = switch_name[:3].lower()
        devices = await self.get_switch_devices(switch_type)
        name_upper = switch_name.upper()
        device = next(
            (d for d in devices if (d.get("name") or "").upper() == name_upper), None
        )
        if not device:
            return None
        location_id = device.get("location_id")
        if not location_id:
            return None
        async with self._client() as client:
            r = await client.get(
                f"{self.base_url}/locations/{location_id}",
                auth=self._auth(),
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
        loc = r.json()
        return {
            "location_id": location_id,
            "location_name": loc.get("name") or loc.get("short_name", ""),
            "location_city": loc.get("city", ""),
            "country": loc.get("country", ""),
        }
