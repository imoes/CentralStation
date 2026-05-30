"""ID-Generator connector.

Auth: Basic Auth — idgen_reader:ippenmedia (read-only)
API base: {base_url}/api/v2/

Simple GETs need no auth; sql-query and writes require Basic Auth.
"""
import urllib.parse

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class IDGeneratorConnector(BaseConnector):
    def _auth(self) -> tuple[str, str]:
        return (
            self.credentials.get("username", "idgen_reader"),
            self.credentials.get("password", "ippenmedia"),
        )

    def _api(self, path: str) -> str:
        """Build full API URL: base_url + /api/v2/ + path."""
        base = (self.base_url or "").rstrip("/")
        return f"{base}/api/v2/{path.lstrip('/')}"

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(self._api("locations"), params={"limit": 1})
                r.raise_for_status()
            data = r.json()
            count = len(data) if isinstance(data, list) else data.get("count", "?")
            return ConnectorTestResult(success=True, message=f"ID-Generator erreichbar ({count} Standorte)")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}", details={"body": e.response.text[:200]})
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_locations(self) -> list[dict]:
        async with self._client(timeout=30.0) as client:
            r = await client.get(self._api("locations"), params={"limit": 0})
            r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("results", [])

    async def resolve_ip_to_location(self, ip: str) -> dict | None:
        """Resolve an IP address to a location via INET_ATON sql-query."""
        query = (
            "SELECT n.network_qdn, n.netmask_len, n.label, n.location_id, "
            "l.name AS location_name, l.city AS location_city "
            "FROM ip_networks n "
            "LEFT JOIN locations l ON n.location_id = l.id "
            f"WHERE INET_ATON('{ip}') BETWEEN INET_ATON(n.network_qdn) "
            "AND INET_ATON(n.network_qdn) + POW(2, 32 - n.netmask_len) - 1 "
            "ORDER BY n.netmask_len DESC LIMIT 1"
        )
        async with self._client() as client:
            r = await client.get(
                self._api("locations"),
                auth=self._auth(),
                params={"limit": 0, "sql-query": query},
            )
            r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("results", [])
        if not rows:
            return None
        row = rows[0]
        return {
            "location_id": row.get("location_id"),
            "location_name": row.get("location_name", ""),
            "location_city": row.get("location_city", ""),
        }

    async def get_switch_devices(self, switch_type: str) -> list[dict]:
        """switch_type: 'nsa' | 'nss' | 'nsc'"""
        endpoint_map = {
            "nsa": "switchnsadevices",
            "nss": "switchnssdevices",
            "nsc": "switchnscdevices",
        }
        endpoint = endpoint_map.get(switch_type.lower(), "switchnsadevices")
        async with self._client(timeout=30.0) as client:
            r = await client.get(self._api(endpoint), params={"limit": 0})
            r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("results", [])

    async def resolve_host_to_location(self, hostname: str) -> dict | None:
        """Resolve a hostname to a location via the virt_servers table.

        Falls back to IP-based resolution if the hostname cannot be found in virt_servers.
        """
        # Try virt_servers table first (covers VPP*/VVE* and other virtual hosts)
        short = hostname.split(".")[0]
        sql = f"SELECT v.location_id, l.name as loc_name, l.city FROM virt_servers v LEFT JOIN locations l ON v.location_id = l.id WHERE v.name = '{short}' LIMIT 1"
        try:
            async with self._client(timeout=10.0) as client:
                r = await client.get(
                    self._api("locations"),
                    params={"limit": 0, "sql-query": sql},
                    auth=self._auth(),
                )
            if r.status_code == 200:
                rows = r.json()
                if rows:
                    row = rows[0]
                    return {
                        "location_id": row.get("location_id"),
                        "location_name": row.get("loc_name", ""),
                        "location_city": row.get("city", ""),
                    }
        except Exception:
            pass

        # Fallback: DNS + IP lookup
        try:
            import socket
            ip = socket.gethostbyname(hostname)
            return await self.resolve_ip_to_location(ip)
        except Exception:
            return None

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
            r = await client.get(self._api(f"locations/{location_id}"))
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
