"""Read-only diagnostic tool providers for the AI agent.

Architecture: SSH-ready provider registry.
Today only API-based providers exist; a SshReadOnlyProvider can be added later
by implementing the same DiagnosticProvider protocol — no callers change.

All providers are guaranteed read-only. The `read_only` flag on DiagnosticResult
is an invariant, not a suggestion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    tool: str
    host: str
    summary: str
    data: dict = field(default_factory=dict)
    read_only: bool = True   # INVARIANT — always True; mutation is not allowed

    def to_llm_text(self) -> str:
        return f"[{self.tool}] {self.host}: {self.summary}"


class DiagnosticProvider(Protocol):
    name: str

    async def available_for(self, host: str, db: Any) -> bool:
        ...

    async def run(self, host: str, db: Any) -> DiagnosticResult:
        ...


# ── Provider implementations ─────────────────────────────────────────────────

class CheckMKStatusProvider:
    """Current service states from CheckMK REST API."""
    name = "checkmk_status"

    async def available_for(self, host: str, db: Any) -> bool:
        from sqlalchemy import select
        from app.models.connector import ConnectorConfig
        r = await db.execute(
            select(ConnectorConfig)
            .where(ConnectorConfig.type == "checkmk", ConnectorConfig.enabled.is_(True))
            .limit(1)
        )
        return r.scalar_one_or_none() is not None

    async def run(self, host: str, db: Any) -> DiagnosticResult:
        from sqlalchemy import select
        from app.models.connector import ConnectorConfig
        from app.core.security import decrypt_credentials
        from app.services.connectors.checkmk import CheckMKConnector

        r = await db.execute(
            select(ConnectorConfig)
            .where(ConnectorConfig.type == "checkmk", ConnectorConfig.enabled.is_(True))
            .limit(1)
        )
        conn = r.scalar_one_or_none()
        if not conn:
            return DiagnosticResult(self.name, host, "CheckMK connector not configured.")

        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            cmk = CheckMKConnector(base_url=conn.base_url, credentials=creds)
            problems = await cmk.get_problems(time_range_minutes=60)
            host_problems = [p for p in problems if host.lower() in (p.get("host") or "").lower()]
            if not host_problems:
                summary = f"Keine aktiven Probleme für {host} in CheckMK."
            else:
                # get_problems() returns "service" key, not "title"
                lines = [
                    f"{p.get('severity','?').upper()}: {p.get('service') or p.get('title','?')}"
                    for p in host_problems[:5]
                ]
                summary = f"{len(host_problems)} aktive(s) Problem(e): " + " | ".join(lines)
            return DiagnosticResult(self.name, host, summary, {"problems": host_problems[:5]})
        except Exception as e:
            log.debug("diagnostics checkmk_status failed: %s", e)
            return DiagnosticResult(self.name, host, f"CheckMK-Abfrage fehlgeschlagen: {e}")


class MetricsProvider:
    """Recent CPU/RAM/Disk metrics from cs-metrics-checkmk."""
    name = "metrics"

    async def available_for(self, host: str, db: Any) -> bool:
        return True

    async def run(self, host: str, db: Any) -> DiagnosticResult:
        from app.services.metrics_collector import query_metrics_for_host
        try:
            metrics = await query_metrics_for_host(host, hours=2)
            if not metrics:
                return DiagnosticResult(self.name, host, f"Keine Metriken für {host} in den letzten 2h.")
            latest: dict[str, float] = {}
            for m in metrics:
                mid = m.get("metric") or ""
                latest[mid] = float(m.get("value") or 0)
            parts = []
            if "mem_used_percent" in latest:
                parts.append(f"RAM {latest['mem_used_percent']:.0f}%")
            if "fs_used_percent" in latest:
                parts.append(f"Disk {latest['fs_used_percent']:.0f}%")
            if "load1" in latest:
                parts.append(f"CPU-Load {latest['load1']:.1f}")
            summary = ", ".join(parts) if parts else "Metriken geladen, keine Standardwerte."
            return DiagnosticResult(self.name, host, summary, {"latest": latest})
        except Exception as e:
            log.debug("diagnostics metrics failed: %s", e)
            return DiagnosticResult(self.name, host, f"Metriken nicht verfügbar: {e}")


class RecentLogsProvider:
    """Recent feed items for the host from OpenSearch."""
    name = "recent_logs"

    async def available_for(self, host: str, db: Any) -> bool:
        return True

    async def run(self, host: str, db: Any) -> DiagnosticResult:
        from app.services.feed_index import search
        try:
            items = await search(host=host, exclude_resolved=False, size=5)
            if not items:
                return DiagnosticResult(self.name, host, f"Keine Feed-Einträge für {host} gefunden.")
            lines = [f"{i.get('severity','?').upper()}: {i.get('title','')[:80]}" for i in items[:5]]
            summary = f"{len(items)} Feed-Einträge: " + " | ".join(lines[:3])
            return DiagnosticResult(self.name, host, summary, {"items": lines})
        except Exception as e:
            log.debug("diagnostics recent_logs failed: %s", e)
            return DiagnosticResult(self.name, host, f"Log-Abfrage fehlgeschlagen: {e}")


class TopologyProvider:
    """Host location + co-located systems via NetBox/ID-Generator."""
    name = "topology"

    async def available_for(self, host: str, db: Any) -> bool:
        return True

    async def run(self, host: str, db: Any) -> DiagnosticResult:
        try:
            from sqlalchemy import select
            from app.models.connector import ConnectorConfig
            from app.core.security import decrypt_credentials
            from app.services.connectors.id_generator import IDGeneratorConnector
            r = await db.execute(
                select(ConnectorConfig)
                .where(ConnectorConfig.type == "id_generator", ConnectorConfig.enabled.is_(True))
                .limit(1)
            )
            conn = r.scalar_one_or_none()
            if not conn:
                return DiagnosticResult(self.name, host, "ID-Generator nicht konfiguriert.")
            creds = decrypt_credentials(conn.encrypted_credentials)
            idgen = IDGeneratorConnector(base_url=conn.base_url, credentials=creds)
            loc = await idgen.resolve_host_to_location(host)
            if not loc:
                return DiagnosticResult(self.name, host, f"Kein Standort für {host} ermittelbar.")
            return DiagnosticResult(
                self.name, host,
                f"Standort: {loc.get('location_name','?')} ({loc.get('city','?')})",
                loc,
            )
        except Exception as e:
            log.debug("diagnostics topology failed: %s", e)
            return DiagnosticResult(self.name, host, f"Topologie nicht verfügbar: {e}")


# ── Provider registry ─────────────────────────────────────────────────────────
# SSH provider placeholder — add SshReadOnlyProvider() here in Phase 3.
DIAGNOSTIC_PROVIDERS: list[Any] = [
    CheckMKStatusProvider(),
    MetricsProvider(),
    RecentLogsProvider(),
    TopologyProvider(),
    # SshReadOnlyProvider(),  # ← Phase 3: command allowlist, Fernet key, full audit
]


async def run_diagnostics(host: str, db: Any) -> list[DiagnosticResult]:
    """Run all available diagnostic providers sequentially and return results.

    Sequential execution avoids SQLAlchemy 'concurrent operations not permitted'
    errors that occur when multiple coroutines share the same AsyncSession.
    """
    from app.core.database import AsyncSessionLocal

    results: list[DiagnosticResult] = []
    for provider in DIAGNOSTIC_PROVIDERS:
        # Each provider gets its own session to avoid concurrent-access errors
        try:
            async with AsyncSessionLocal() as session:
                if not await provider.available_for(host, session):
                    continue
                result = await provider.run(host, session)
                if result is not None:
                    results.append(result)
        except Exception as e:
            log.warning("diagnostics provider %s failed: %s", provider.name, e)
    return results
