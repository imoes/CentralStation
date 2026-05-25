"""Wazuh REST API connector.

Excluded rule IDs and FIM paths are configurable via the connector credentials
(Settings → Connectors → Wazuh) and stored encrypted in the database.
Defaults are applied when not configured:

  excluded_rule_ids (default):
    503, 504, 533, 591, 5402, 5501, 5502, 5715

  excluded_fim_paths (default):
    /etc/cmk-update-agent.state, /etc/patchmon/config.yml


Wazuh 4.14+: The /alerts endpoint was removed. Alerts are now in the
Wazuh Indexer (OpenSearch). This connector supports two modes:

  1. Indexer mode (preferred): if credentials contain `indexer_url`,
     alerts are fetched directly from the OpenSearch index.
  2. Legacy mode: falls back to GET /alerts on the Manager API
     (works on Wazuh < 4.14 only).

Credentials dict keys:
  username / password       — Wazuh Manager API (for auth + test_connection)
  indexer_url               — Wazuh Indexer base URL, e.g. http://wazuh-indexer-1:9200
  indexer_username          — Indexer user (default: "admin")
  indexer_password          — Indexer password
"""
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


def _to_severity(level: int) -> str:
    if level < 4:
        return "info"
    if level < 7:
        return "low"
    if level < 10:
        return "medium"
    if level < 13:
        return "high"
    return "critical"


def _credential_list(value, defaults: list[str]) -> list[str]:
    """Return credential value as non-empty string list, otherwise defaults."""
    if isinstance(value, list):
        vals = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        vals = [v.strip() for v in value.splitlines() if v.strip()]
    else:
        vals = []
    return vals or defaults


class WazuhConnector(BaseConnector):
    def __init__(self, base_url: str | None, credentials: dict):
        super().__init__(base_url, credentials)
        self._resolved_api_base: str | None = None

    # ── Manager API helpers ─────────────────────────────────────────────────

    def _api_base_candidates(self) -> list[str]:
        base = (self.base_url or "").rstrip("/")
        if not base:
            return []
        parsed = urlparse(base)
        candidates = [base]
        if parsed.port is None and parsed.hostname:
            netloc = f"{parsed.hostname}:55000"
            with_port = urlunparse(parsed._replace(netloc=netloc))
            candidates.append(with_port.rstrip("/"))
        unique: list[str] = []
        for c in candidates:
            if c not in unique:
                unique.append(c)
        return unique

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        last_error: httpx.HTTPStatusError | None = None
        for api_base in self._api_base_candidates():
            r = await client.post(
                f"{api_base}/security/user/authenticate?raw=true",
                auth=(user, password),
            )
            try:
                r.raise_for_status()
                self._resolved_api_base = api_base
                return r.text.strip()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if r.status_code != 404:
                    raise
        if last_error:
            raise last_error
        raise RuntimeError("No Wazuh API base candidate available")

    # ── Indexer (OpenSearch) helpers ────────────────────────────────────────

    def _indexer_base(self) -> str | None:
        url = (self.credentials.get("indexer_url") or "").rstrip("/")
        return url or None

    async def _query_indexer(
        self,
        client: httpx.AsyncClient,
        index: str,
        body: dict,
    ) -> dict:
        base = self._indexer_base()
        iuser = self.credentials.get("indexer_username") or "admin"
        ipass = self.credentials.get("indexer_password") or ""
        r = await client.post(
            f"{base}/{index}/_search",
            auth=(iuser, ipass),
            headers={"Content-Type": "application/json"},
            content=json.dumps(body),
        )
        r.raise_for_status()
        return r.json()

    # ── Public API ──────────────────────────────────────────────────────────

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                token = await self._get_token(client)
                api_base = self._resolved_api_base or self._api_base_candidates()[0]
                r = await client.get(
                    f"{api_base}/manager/info",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                version = (
                    r.json()
                    .get("data", {})
                    .get("affected_items", [{}])[0]
                    .get("version", "?")
                )

            msg = f"Wazuh {version} erreichbar"

            # Also test indexer if configured
            indexer_url = self._indexer_base()
            if indexer_url:
                try:
                    iuser = self.credentials.get("indexer_username") or "admin"
                    ipass = self.credentials.get("indexer_password") or ""
                    async with httpx.AsyncClient(timeout=10.0, verify=False) as ic:
                        ri = await ic.get(indexer_url, auth=(iuser, ipass))
                        ri.raise_for_status()
                    msg += " · Indexer OK"
                except Exception as ie:
                    msg += f" · Indexer Fehler: {ie}"

            return ConnectorTestResult(success=True, message=msg)
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(
                success=False,
                message=f"HTTP {e.response.status_code}",
                details={"response": e.response.text[:300]},
            )
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_alerts(
        self,
        limit: int = 100,
        min_level: int = 7,
        time_range_minutes: int = 60,
    ) -> list[dict]:
        indexer_url = self._indexer_base()
        if indexer_url:
            return await self._get_alerts_from_indexer(limit, min_level, time_range_minutes)
        return await self._get_alerts_from_manager(limit, min_level)

    async def _get_alerts_from_indexer(
        self,
        limit: int,
        min_level: int,
        time_range_minutes: int,
    ) -> list[dict]:
        since = (
            datetime.now(timezone.utc) - timedelta(minutes=time_range_minutes)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # Load from connector credentials — configurable via GUI.
        # Falls back to hardcoded defaults when not set.
        _EXCLUDED_RULE_IDS = _credential_list(
            self.credentials.get("excluded_rule_ids"),
            ["503", "504", "533", "591", "5402", "5501", "5502", "5715"],
        )
        _EXCLUDED_FIM_PATHS = _credential_list(
            self.credentials.get("excluded_fim_paths"),
            ["/etc/cmk-update-agent.state", "/etc/patchmon/config.yml"],
        )

        query = {
            "size": limit,
            "sort": [{"timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"timestamp": {"gte": since}}},
                        {"range": {"rule.level": {"gte": min_level}}},
                    ],
                    "must_not": [
                        {"terms": {"rule.id": _EXCLUDED_RULE_IDS}},
                        {"terms": {"data.syscheck.path": _EXCLUDED_FIM_PATHS}},
                    ],
                }
            },
            "_source": [
                "timestamp",
                "rule.level",
                "rule.description",
                "rule.id",
                "agent.name",
                "agent.ip",
                "full_log",
                "location",
                "data",
            ],
        }

        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            data = await self._query_indexer(client, "wazuh-alerts-*", query)

        results = []
        for hit in data.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            rule = src.get("rule", {})
            agent = src.get("agent", {})
            level = rule.get("level", 0)
            agent_name = agent.get("name", "unknown")
            rule_id = str(rule.get("id", "0"))
            desc = rule.get("description", "Wazuh Alert")
            dedup_key = f"{agent_name}:{rule_id}"
            results.append({
                "severity": _to_severity(level),
                "title": f"{agent_name} — {desc}",
                "body": src.get("full_log", ""),
                "external_id": dedup_key,
                "metadata": {
                    "agent": agent_name,
                    "agent_ip": agent.get("ip", ""),
                    "rule_id": rule_id,
                    "rule_level": level,
                    "location": src.get("location", ""),
                },
                "timestamp": src.get("timestamp", ""),
            })
        return results

    async def _get_alerts_from_manager(
        self,
        limit: int,
        min_level: int,
    ) -> list[dict]:
        """Legacy: Wazuh < 4.14 Manager /alerts endpoint."""
        async with self._client(timeout=30.0) as client:
            token = await self._get_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            api_base = self._resolved_api_base or self._api_base_candidates()[0]
            r = await client.get(
                f"{api_base}/alerts",
                headers=headers,
                params={
                    "limit": limit,
                    "sort": "-timestamp",
                    "q": f"rule.level>={min_level}",
                },
            )
            r.raise_for_status()

        results = []
        for item in r.json().get("data", {}).get("affected_items", []):
            rule = item.get("rule", {})
            level = rule.get("level", 0)
            agent = item.get("agent", {})
            agent_name = agent.get("name", "unknown")
            rule_id = str(rule.get("id", "0"))
            desc = rule.get("description", "Wazuh Alert")
            dedup_key = f"{agent_name}:{rule_id}"
            results.append({
                "severity": _to_severity(level),
                "title": f"{agent_name} — {desc}",
                "body": item.get("full_log", ""),
                "external_id": dedup_key,
                "metadata": {
                    "agent": agent_name,
                    "rule_id": rule_id,
                    "rule_level": level,
                },
                "timestamp": item.get("timestamp", ""),
            })
        return results
