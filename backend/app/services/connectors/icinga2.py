"""Icinga2 REST API connector.

Auth:     HTTP Basic Auth (Icinga2 ApiUser)         — credentials: username, password
API base: https://<host>:5665                       — default Icinga2 API port
Certs:    Icinga2 ships a self-signed cert by default → BaseConnector uses verify=False.

Built per the CentralStation Connector-SDK (see README → "Eigene Konnektoren schreiben").
Monitoring connector → implements get_problems() returning the unified problem schema:
  {severity, host, service, output, acknowledged, last_state_change, host_address, metadata}

Service states (Icinga2):  0 OK · 1 WARNING · 2 CRITICAL · 3 UNKNOWN
Host states (Icinga2):     0 UP · 1 DOWN
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

# Icinga2 service state → CentralStation severity (critical|high|medium|low|info)
_STATE_SEVERITY: dict[int, str] = {
    0: "info",      # OK (only seen if explicitly queried; problems filter excludes it)
    1: "medium",    # WARNING
    2: "critical",  # CRITICAL
    3: "medium",    # UNKNOWN
}


class Icinga2Connector(BaseConnector):
    def _headers(self) -> dict:
        return {"Accept": "application/json"}

    def _auth(self) -> tuple[str, str]:
        return (
            self.credentials.get("username", ""),
            self.credentials.get("password", ""),
        )

    def _api(self, path: str) -> str:
        # base_url defaults to https://<host>:5665 ; tolerate a trailing /v1
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return f"{base}/v1{path}"

    async def test_connection(self) -> ConnectorTestResult:
        """Verify reachability + auth via the IcingaApplication status endpoint."""
        try:
            async with self._client() as client:
                r = await client.get(
                    self._api("/status/IcingaApplication"),
                    headers=self._headers(),
                    auth=self._auth(),
                )
            r.raise_for_status()
            data = r.json()
            version = ""
            try:
                version = data["results"][0]["status"]["icingaapplication"]["app"]["version"]
            except Exception:
                pass
            msg = f"Icinga2 erreichbar{f' (v{version})' if version else ''}"
            return ConnectorTestResult(success=True, message=msg)
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(
                success=False,
                message=f"HTTP {e.response.status_code}",
                details={"response_text": e.response.text[:300]},
            )
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_problems(self) -> list[dict]:
        """Return open service problems in the unified CentralStation schema.

        Uses the documented Icinga2 query pattern: POST + X-HTTP-Method-Override:GET
        with a filter, attribute selection and host joins (so we get the host address
        and host state in one round-trip).
        """
        body = {
            # Open, unhandled service problems on hosts that are UP.
            "filter": (
                "service.state!=0 && service.acknowledgement==0 "
                "&& service.downtime_depth==0.0 && host.state==0"
            ),
            "attrs": [
                "state", "plugin_output", "last_state_change",
                "acknowledgement", "downtime_depth", "display_name",
            ],
            "joins": ["host.name", "host.address", "host.state", "host.vars"],
        }
        headers = {**self._headers(), "X-HTTP-Method-Override": "GET",
                   "Content-Type": "application/json"}
        async with self._client(timeout=30.0) as client:
            r = await client.post(
                self._api("/objects/services"),
                headers=headers,
                auth=self._auth(),
                json=body,
            )
        r.raise_for_status()

        results: list[dict] = []
        for obj in r.json().get("results", []):
            attrs = obj.get("attrs", {}) or {}
            joins = obj.get("joins", {}) or {}
            host_join = joins.get("host", {}) or {}

            # obj["name"] is "host!service"; prefer the joined host name when present
            raw_name = obj.get("name", "")
            host = host_join.get("name") or (raw_name.split("!")[0] if "!" in raw_name else "")
            service = attrs.get("display_name") or (
                raw_name.split("!", 1)[1] if "!" in raw_name else raw_name
            )

            state = int(attrs.get("state", 2))
            lsc = attrs.get("last_state_change")
            last_change = None
            if isinstance(lsc, (int, float)) and lsc > 0:
                last_change = datetime.fromtimestamp(lsc, tz=timezone.utc).isoformat()

            host_vars = host_join.get("vars") or {}
            results.append({
                "severity": _STATE_SEVERITY.get(state, "medium"),
                "host": host,
                "service": service,
                "output": (attrs.get("plugin_output") or "")[:500],
                "acknowledged": bool(attrs.get("acknowledgement")),
                "last_state_change": last_change,
                "host_address": host_join.get("address", ""),
                "metadata": {
                    # CheckMK-style filter fields — populate from Icinga host vars
                    # when available so OS/location filters work uniformly.
                    "os": str(host_vars.get("os", "")) if isinstance(host_vars, dict) else "",
                    "location": str(host_vars.get("location", "")) if isinstance(host_vars, dict) else "",
                    "icinga_state": state,
                },
            })
        return results
