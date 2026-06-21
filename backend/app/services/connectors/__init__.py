import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class _GenericConnector(BaseConnector):
    """Simple HTTP reachability check — used for mcp_server connectors."""

    async def test_connection(self) -> ConnectorTestResult:
        if not self.base_url:
            return ConnectorTestResult(success=False, message="Base URL fehlt")
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                r = await client.get(self.base_url)
            return ConnectorTestResult(success=True, message=f"Erreichbar (HTTP {r.status_code})")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))


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
    }
    cls = mapping.get(connector_type)
    if not cls:
        raise ValueError(f"Unknown connector type: {connector_type}")
    return cls(base_url=base_url, credentials=credentials)
