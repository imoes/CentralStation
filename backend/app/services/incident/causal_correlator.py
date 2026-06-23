"""Kausale Incident-Korrelation via it-aikb Service-Abhängigkeiten.

Wenn ein neuer Incident für Host X erstellt wird:
  1. it-aikb OpenSearch nach KB-Seite für X befragen
  2. service_dependencies lesen (was benötigt X?)
  3. Für jede Dependency: gibt es bereits einen offenen Incident?
  4. Wenn ja: causal_context im neuen Incident setzen

Das causal_context-Feld wird im Frontend als
"Wahrscheinliche Ursache"-Panel angezeigt.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


def _host_to_service(host: str) -> str:
    """'cue0175.ippen.media' → 'cue0175', 'graylog.ippen.media' → 'graylog'"""
    return host.split(".")[0].lower()


async def _load_aikb_connector(db: AsyncSession):
    """Lädt den aktiven AIKBConnector (gibt None zurück wenn nicht konfiguriert)."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.aikb import AIKBConnector

    r = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "aikb",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    row = r.scalars().first()
    if not row:
        return None
    return AIKBConnector(
        base_url=row.base_url,
        credentials=decrypt_credentials(row.encrypted_credentials),
    )


async def find_causal_incidents(
    host: str,
    incident_id: str,
    db: AsyncSession,
) -> list[dict]:
    """Sucht kausale Ursachen für einen Incident auf Host X.

    Fragt it-aikb OpenSearch nach service_dependencies für X und prüft ob
    für eine dieser Dependencies bereits ein offener Incident existiert.

    Returns: Liste von {"service", "incident_id", "likely_cause", "started_at"}
             Leere Liste wenn kein AIKB-Connector oder keine Abhängigkeiten bekannt.
    """
    from app.models.workflow import Incident

    conn = await _load_aikb_connector(db)
    if not conn:
        return []

    service_name = _host_to_service(host)
    if not service_name or len(service_name) < 3:
        return []

    try:
        # KB-Seite für den Host in it-aikb suchen
        hits = await conn.search_opensearch(
            query=f"[KB] {service_name}",
            space_keys=["002IT", "MYC"],
            size=5,
        )
    except Exception as exc:
        log.debug("causal_correlator: AIKB-Suche fehlgeschlagen für %s: %s", host, exc)
        return []

    if not hits:
        log.debug("causal_correlator: keine KB-Seite für service=%s host=%s", service_name, host)
        return []

    # Bestes Match: Seite deren Titel den Service-Namen enthält
    best = next(
        (h for h in hits if service_name in h.get("title", "").lower()),
        hits[0],
    )

    # service_dependencies aus strukturiertem Feld lesen (nach Enrichment-Lauf)
    # Fallback: leere Liste — Enricher muss erst gelaufen sein
    service_deps: list[str] = best.get("service_dependencies") or []

    if not service_deps:
        log.debug(
            "causal_correlator: keine service_dependencies für service=%s "
            "(Enricher noch nicht gelaufen?)", service_name,
        )
        return []

    causal: list[dict] = []

    for dep in service_deps:
        try:
            # Offener Incident für diese Dependency?
            stmt = (
                select(Incident)
                .where(
                    Incident.primary_host.ilike(f"%{dep}%"),
                    Incident.resolved_at.is_(None),
                    Incident.status.in_(("open", "investigating")),
                    Incident.id != incident_id,
                )
                .order_by(Incident.created_at.asc())
                .limit(1)
            )
            result = await db.execute(stmt)
            related = result.scalar_one_or_none()

            if related:
                causal.append({
                    "service": dep,
                    "incident_id": str(related.id),
                    "likely_cause": True,
                    "started_at": related.created_at.isoformat(),
                    "host": related.primary_host,
                })
                log.info(
                    "causal_correlator: incident %s (host=%s) könnte verursacht sein durch "
                    "Incident %s (dep=%s, host=%s)",
                    incident_id[:8], host, str(related.id)[:8], dep, related.primary_host,
                )
        except Exception as exc:
            log.debug("causal_correlator: Dep-Check fehlgeschlagen für dep=%s: %s", dep, exc)

    return causal


async def enrich_incident_causal_context(
    incident_id: str,
    host: str,
    db: AsyncSession,
) -> None:
    """Ergänzt causal_context für einen Incident (best-effort, nicht-blockierend).

    Wird nach Incident-Erstellung aus correlate_docs aufgerufen.
    Fehler werden geloggt aber nicht weitergeworfen.
    """
    from app.models.workflow import Incident

    try:
        causal = await find_causal_incidents(host, incident_id, db)
        if not causal:
            return

        # Incident aus DB laden und causal_context setzen
        result = await db.execute(
            select(Incident).where(Incident.id == incident_id)
        )
        incident = result.scalar_one_or_none()
        if incident:
            incident.causal_context = causal
            log.info(
                "causal_correlator: causal_context gesetzt für incident %s → %s",
                incident_id[:8], [c["service"] for c in causal],
            )
    except Exception as exc:
        log.debug("causal_correlator: enrich_incident_causal_context fehlgeschlagen: %s", exc)
