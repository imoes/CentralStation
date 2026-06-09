from app.services.connectors.base import BaseConnector


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
    }
    cls = mapping.get(connector_type)
    if not cls:
        raise ValueError(f"Unknown connector type: {connector_type}")
    return cls(base_url=base_url, credentials=credentials)
