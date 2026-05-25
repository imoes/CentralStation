"""Graylog REST API connector.

Auth: Basic Auth (username:password)
Ref: llm-graylog-analyse/graylog_analyzer.py (Views Search API + HyDE multi-query + Dedup)
"""
import base64
import hashlib
import re

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

# Timestamp prefix patterns to strip before dedup key generation
# (ref: llm-graylog-analyse/graylog_analyzer.py:_TS_PATTERNS)
_TS_PATTERNS = [re.compile(p) for p in [
    r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\s\d+\s",          # MariaDB
    r"\[\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3}\]\s*",      # Bracketed
    r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3}\s-\s",       # Actor / logback
    r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}\sUTC\s",    # PostgreSQL
    r"^[A-Z]\d{4}\s\d{2}:\d{2}:\d{2}\.\d{6}\s+\d+\s",          # Go klog
    r"^\[\d+\]\s",                                                # PID brackets
    r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}\s",         # Spring Boot
]]

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
    # Durations: 78.706µs / 1.5ms / 300ns — common in Go/Java logs, always dynamic
    (re.compile(r'\d+\.?\d*(?:µs|us|ms|ns)\b'), '<DUR>'),
    # Hex memory addresses: 0x7f3a4b2c1d5e
    (re.compile(r'\b0x[0-9a-fA-F]+\b'), '<ADDR>'),
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


def strip_timestamps(text: str) -> str:
    for pattern in _TS_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


def make_dedup_key(source: str, message: str) -> str:
    # Strip leading timestamps before normalization for more stable dedup keys
    text = f"{source}:{strip_timestamps(message)}"
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

    async def search_messages_multi(
        self,
        queries: list[str],
        time_range_seconds: int = 600,
        limit_per_query: int = 50,
    ) -> list[dict]:
        """Run multiple Graylog queries in a single sync API call.

        Results from all queries are merged and deduplicated by dedup_key.
        Query priority is preserved: earlier queries win on duplicate keys.
        Ref: llm-graylog-analyse/graylog_analyzer.py:fetch_logs (HyDE 3-query approach)
        """
        graylog_queries = [
            {
                "id": f"q{i}",
                "timerange": {"type": "relative", "range": time_range_seconds},
                "query": {"type": "elasticsearch", "query_string": q},
                "search_types": [{
                    "id": f"st{i}",
                    "type": "messages",
                    "limit": limit_per_query,
                    "offset": 0,
                    "sort": [{"field": "timestamp", "order": "DESC"}],
                    "streams": [],
                }],
            }
            for i, q in enumerate(queries)
        ]
        payload = {"queries": graylog_queries}
        async with self._client(timeout=30.0) as client:
            r = await client.post(
                f"{self.base_url}/api/views/search/sync",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()

        all_results = r.json().get("results", {})
        seen_dedup: set[str] = set()
        messages: list[dict] = []

        for i in range(len(queries)):
            search_type_result = (
                all_results.get(f"q{i}", {})
                .get("search_types", {})
                .get(f"st{i}", {})
            )
            for msg in search_type_result.get("messages", []):
                m = msg.get("message", {})
                raw_message = m.get("message", "")
                source = m.get("source", "")
                dk = make_dedup_key(source, raw_message)
                if dk in seen_dedup:
                    continue
                seen_dedup.add(dk)
                # Physical host candidates (in priority order):
                # 1. source     — GELF 'host' field; for Docker GELF driver = Docker daemon host
                # 2. hostname   — set by some GELF shippers as an extra field
                # 3. host_name  — Graylog flattened version of 'host.name' from filebeat
                # 4. beat_hostname — old filebeat 'beat.hostname' field
                # We collect all non-empty, deduplicated values so callers can
                # fall through to the first one that is known in CheckMK.
                _host_cands: list[str] = []
                for _hf in (
                    source,
                    m.get("hostname", ""),
                    m.get("host_name", ""),
                    m.get("beat_hostname", ""),
                ):
                    if _hf and _hf not in _host_cands:
                        _host_cands.append(_hf)

                messages.append({
                    "id": m.get("_id", ""),
                    "source": source,
                    "host_candidates": _host_cands,
                    "container_name": m.get("container_name") or m.get("container_tag") or "",
                    "message": raw_message,
                    "timestamp": m.get("timestamp", ""),
                    "level": m.get("level", 6),
                    "facility": m.get("facility", ""),
                    "http_response_code": m.get("http_response_code"),
                    "dedup_key": dk,
                    "vendor": detect_vendor(source, raw_message),
                    "hyde_relevant": bool(m.get("hyde_relevant")),
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
