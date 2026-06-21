import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class _GenericConnector(BaseConnector):
    """Reachability check for mcp_server connectors — transport-aware."""

    async def test_connection(self) -> ConnectorTestResult:
        if not self.base_url:
            return ConnectorTestResult(success=False, message="Base URL fehlt")
        transport = self.credentials.get("transport", "streamable-http")
        token = self.credentials.get("token", "")
        headers = {"Authorization": token} if token else {}
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                if transport == "sse":
                    # SSE: GET with Accept: text/event-stream
                    headers["Accept"] = "text/event-stream"
                    r = await client.get(self.base_url, headers=headers)
                    if r.status_code == 200 or r.status_code == 405:
                        return ConnectorTestResult(success=True, message=f"SSE-Endpunkt erreichbar (HTTP {r.status_code})")
                    return ConnectorTestResult(success=r.status_code < 500,
                                              message=f"HTTP {r.status_code}")
                else:
                    # streamable-http: POST with minimal MCP initialize ping
                    headers["Content-Type"] = "application/json"
                    # MCP streamable-http spec requires text/event-stream in Accept
                    # (server may respond with SSE stream for long-running ops).
                    headers["Accept"] = "application/json, text/event-stream"
                    r = await client.post(
                        self.base_url, headers=headers,
                        json={"jsonrpc": "2.0", "method": "initialize", "id": 1,
                              "params": {"protocolVersion": "2024-11-05",
                                         "capabilities": {},
                                         "clientInfo": {"name": "cs-test", "version": "1"}}},
                    )
                    if r.status_code in (200, 202):
                        return ConnectorTestResult(success=True, message=f"MCP streamable-http erreichbar (HTTP {r.status_code})")
                    return ConnectorTestResult(success=False, message=f"HTTP {r.status_code}: {r.text[:100]}")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))


class _SshConnector(BaseConnector):
    """Validates SSH credentials for the per-user Hermes/Werkbank container."""

    async def test_connection(self) -> ConnectorTestResult:
        username = self.credentials.get("username", "")
        if not username:
            return ConnectorTestResult(success=False, message="SSH-Benutzername fehlt")
        has_key = bool(self.credentials.get("private_key", "").strip())
        has_pass = bool(self.credentials.get("password", "").strip())
        if not has_key and not has_pass:
            return ConnectorTestResult(success=False, message="Key oder Passwort fehlt")
        mode = "Key" if has_key else "Passwort"
        return ConnectorTestResult(success=True, message=f"SSH ({mode}) für Benutzer '{username}' gespeichert")


class _AwxNgConnector(BaseConnector):
    """Basic-Auth health check for AWX-NG MCP connectors."""

    async def test_connection(self) -> ConnectorTestResult:
        if not self.base_url:
            return ConnectorTestResult(success=False, message="Base URL fehlt")
        username = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                r = await client.get(
                    f"{self.base_url}/api/v2/ping/",
                    auth=(username, password) if username else None,
                )
            if r.status_code == 200:
                return ConnectorTestResult(success=True, message="AWX-NG erreichbar")
            if r.status_code == 401:
                return ConnectorTestResult(success=False, message="Authentifizierung fehlgeschlagen (Benutzername/Passwort prüfen)")
            return ConnectorTestResult(success=False, message=f"HTTP {r.status_code}")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))


def get_connector(connector_type: str, base_url: str | None, credentials: dict) -> BaseConnector:
    from app.services.connectors.checkmk import CheckMKConnector
    from app.services.connectors.graylog import GraylogConnector
    from app.services.connectors.wazuh import WazuhConnector
    from app.services.connectors.jira import JiraConnector
    from app.services.connectors.o365 import O365Connector
    from app.services.connectors.teams import TeamsConnector
    from app.services.connectors.prometheus import PrometheusConnector
    from app.services.connectors.netbox import NetBoxConnector
    from app.services.connectors.id_generator import IDGeneratorConnector
    from app.services.connectors.icinga2 import Icinga2Connector
    from app.services.connectors.coroot import CorootConnector
    from app.services.connectors.aikb import AIKBConnector
    from app.services.connectors.smtp import SMTPConnector
    from app.services.connectors.gitlab import GitLabConnector
    from app.services.connectors.awx import AWXConnector
    from app.services.connectors.llm import LLMConnector

    mapping = {
        "checkmk": CheckMKConnector,
        "graylog": GraylogConnector,
        "wazuh": WazuhConnector,
        "icinga2": Icinga2Connector,
        "jira": JiraConnector,
        "jira_sd": JiraConnector,
        "o365": O365Connector,
        "teams": TeamsConnector,
        "prometheus": PrometheusConnector,
        "netbox": NetBoxConnector,
        "id_generator": IDGeneratorConnector,
        "coroot": CorootConnector,
        "aikb": AIKBConnector,
        "smtp": SMTPConnector,
        "gitlab": GitLabConnector,
        "awx": AWXConnector,
        "llm": LLMConnector,
        "awx_ng": _AwxNgConnector,
        "mcp_server": _GenericConnector,
        "ssh": _SshConnector,
    }
    cls = mapping.get(connector_type)
    if not cls:
        raise ValueError(f"Unknown connector type: {connector_type}")
    return cls(base_url=base_url, credentials=credentials)
