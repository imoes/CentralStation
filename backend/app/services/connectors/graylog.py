"""Graylog REST API connector.

Auth: Basic Auth (username:password)
Ref: llm-graylog-analyse/graylog_analyzer.py (Views Search API + Dedup)
"""
import base64
import hashlib
import re

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

# Dedup normalization patterns (ref: llm-graylog-analyse/_DEDUP_PATTERNS)
# Order matters: more-specific patterns first.
_DEDUP_PATTERNS = [
    (re.compile(r'\d{1,3}(?:\.\d{1,3}){3}:\d+'), '<IP:PORT>'),     # IP:port before plain IP
    (re.compile(r'\d{1,3}(?:\.\d{1,3}){3}'), '<IP>'),
    (re.compile(r'@[0-9a-fA-F]{6,}'), '@<ID>'),
    (re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'), '<UUID>'),
    (re.compile(r'\b[0-9a-f]{6,}\b'), '<HEX>'),                     # standalone hex IDs
    (re.compile(r'\b\d{5,}\b'), '<ID>'),
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[,\.]\d+'), '<TS>'),
    # Java: FQCN → simple class name  (com.example.Foo → Foo)
    (re.compile(r'\b(?:java|javax|com|org|net|io)\.[\w.]+\.(\w+)'), r'\1'),
    # Java: strip exception/error class labels (noise in log messages)
    (re.compile(r'\b\w+(?:Exception|Error)\b:?\s*'), ''),
]

SWITCH_PATTERN = re.compile(r'\b(nsa\d+|nss\d+|nsc\d+)\b', re.IGNORECASE)
VENDOR_PATTERNS = {
    "Juniper": [re.compile(p) for p in [
        r'junos', r'rt_flow', r'ui_commit', r'snmp_trap_link', r'rpd_',
        r'chassisd', r'eswd', r'dot1x', r'l2ald',
    ]],
    "Cisco": [re.compile(p) for p in [
        r'%link-', r'%sys-', r'%ospf-', r'%bgp-', r'ios\b', r'nexus',
    ]],
    "VMware": [re.compile(p) for p in [
        r'vmkernel', r'vmx\[', r'vpxa', r'vsan', r'esxi',
    ]],
}


def make_dedup_key(source: str, message: str) -> str:
    text = f"{source}:{message}"
    for pattern, replacement in _DEDUP_PATTERNS:
        text = pattern.sub(replacement, text)
    return hashlib.md5(text.encode()).hexdigest()[:16]


def detect_vendor(source: str, message: str) -> str:
    s = (source or "").lower()
    m = (message or "").lower()
    if any(p in s for p in ["nsa", "jun", "srx", "mx0", "ex0", "qfx"]):
        return "Juniper"
    for vendor, patterns in VENDOR_PATTERNS.items():
        if any(p.search(m) for p in patterns):
            return vendor
    return "Unknown"


class GraylogConnector(BaseConnector):
    def _headers(self) -> dict:
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-By": "CentralStation",
        }

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}/api/system",
                    headers=self._headers(),
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="Graylog reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def search_messages(
        self,
        query: str,
        time_range_seconds: int = 3600,
        limit: int = 100,
    ) -> list[dict]:
        payload = {
            "queries": [{
                "id": "q1",
                "timerange": {"type": "relative", "range": time_range_seconds},
                "query": {"type": "elasticsearch", "query_string": query},
                "search_types": [{
                    "id": "st1",
                    "type": "messages",
                    "limit": limit,
                    "offset": 0,
                    "sort": [{"field": "timestamp", "order": "DESC"}],
                    "streams": [],
                }],
            }]
        }
        async with self._client(timeout=30.0) as client:
            r = await client.post(
                f"{self.base_url}/api/views/search/sync",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()

        messages = []
        result = (
            r.json()
            .get("results", {})
            .get("q1", {})
            .get("search_types", {})
            .get("st1", {})
        )
        for msg in result.get("messages", []):
            m = msg.get("message", {})
            messages.append({
                "id": m.get("_id", ""),
                "source": m.get("source", ""),
                "container_name": m.get("container_name") or m.get("container_tag") or "",
                "message": m.get("message", ""),
                "timestamp": m.get("timestamp", ""),
                "level": m.get("level", 6),
                "facility": m.get("facility", ""),
                "dedup_key": make_dedup_key(m.get("source", ""), m.get("message", "")),
                "vendor": detect_vendor(m.get("source", ""), m.get("message", "")),
            })
        return messages

    async def get_switch_events(self, time_range_seconds: int = 3600) -> list[dict]:
        """Fetch Graylog messages from nsa/nss/nsc switches."""
        query = 'source:(nsa* OR nss* OR nsc*)'
        messages = await self.search_messages(query, time_range_seconds, limit=200)

        seen: set[str] = set()
        result = []
        for msg in messages:
            if msg["dedup_key"] in seen:
                continue
            seen.add(msg["dedup_key"])
            source = msg.get("source", "")
            m = SWITCH_PATTERN.search(source)
            switch_name = m.group(0).upper() if m else source.split(".")[0].upper()
            switch_type = switch_name[:3].lower() if switch_name else "nsa"
            result.append({**msg, "switch_name": switch_name, "switch_type": switch_type})
        return result
