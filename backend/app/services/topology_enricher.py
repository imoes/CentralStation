"""Topology enricher — extrahiert Service-Abhängigkeiten aus it-aikb KB-Seiten.

Liest [KB] Confluence-Seiten via AIKBConnector (RAG DeepSearch), erkennt
Service-Abhängigkeiten und schreibt sie als strukturierte Felder
(service_dependencies, dependency_of) via it-aikb REST-API zurück.

Nach dem Enrichment-Lauf kann CentralStation bei Incidents direkt via
it-aikb OpenSearch abfragen welche Services voneinander abhängen.

Aufruf: POST /api/feed/topology/enrich (Admin only) → Background-Task.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# ── Service-Name-Normalisierung ────────────────────────────────────────────────

def _title_to_service(title: str) -> str:
    """'[KB] Graylog - Log Management' → 'graylog'"""
    name = re.sub(r"\[KB\]\s*", "", title).split(" - ")[0].split(" | ")[0].strip()
    return name.lower().replace(" ", "-")


# Bekannte Service-Namen für Pattern-Matching in RAG-Antworten
_KNOWN_SERVICES = [
    "opensearch", "elasticsearch", "mongodb", "mysql", "postgresql", "oracle",
    "keycloak", "ldap", "active-directory", "sssd",
    "haproxy", "keepalived", "nginx", "apache",
    "graylog", "rsyslog", "filebeat", "logspout",
    "checkmk", "nagios", "prometheus",
    "jira", "confluence", "dokuwiki",
    "docker", "kubernetes", "proxmox",
    "redis", "rabbitmq", "kafka",
    "centralstation", "it-aikb", "netbox",
    "ki-server", "llamacpp", "solr",
    "nfs", "smb", "samba", "cifs",
    "dns", "bind", "dnsmasq",
    "ntp", "chrony",
    "smtp", "mailrelay", "postfix",
    "cue", "sap", "wazuh",
]


def _extract_deps_from_text(text: str) -> list[str]:
    """Extrahiert Service-Namen aus RAG-Antwort per Pattern-Matching."""
    text_lower = text.lower()
    found = []
    for svc in _KNOWN_SERVICES:
        if svc in text_lower:
            found.append(svc)
    # Zusätzlich: Hostnamen-Pattern (z.B. opensearch01.ippen.media → opensearch)
    host_pattern = re.compile(
        r"\b([a-z][a-z0-9\-]{2,})\d*(?:\.ippen\.media|\.test\.ippen\.media)?\b"
    )
    for m in host_pattern.finditer(text_lower):
        base = m.group(1).rstrip("0123456789-")
        if len(base) > 3 and base not in found and base not in _KNOWN_SERVICES:
            # Nur hinzufügen wenn Basis-Name wie ein Service klingt
            if any(kw in base for kw in ["sql", "search", "log", "mon", "key", "proxy", "db"]):
                found.append(base)
    return list(dict.fromkeys(found))  # dedupliziert, Reihenfolge behalten


async def _load_aikb_connector(db: Any):
    """Lädt den aktiven AIKBConnector aus der DB (analog topology_builder.py)."""
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


async def enrich_kb_dependencies(db: Any) -> dict:
    """Haupt-Enricher-Funktion.

    1. Alle [KB]-Seiten (Chunk 0) aus it-aikb OpenSearch laden
    2. Pro Seite: RAG DeepSearch für Abhängigkeiten
    3. Erkannte Deps via it-aikb REST-API zurückschreiben
       (service_dependencies + dependency_of in confluence-pages Index)

    Returns: {"pages_found": int, "enriched": int, "skipped": int, "errors": int}
    """
    conn = await _load_aikb_connector(db)
    if not conn:
        log.warning("topology_enricher: kein AIKB-Connector konfiguriert")
        return {"pages_found": 0, "enriched": 0, "skipped": 0, "errors": 0}

    # Alle [KB]-Seiten abrufen (size=200, Chunk 0)
    pages = await conn.search_opensearch(
        query="[KB] Abhängigkeiten Service",
        space_keys=["002IT", "MYC"],
        size=200,
    )
    if not pages:
        log.info("topology_enricher: keine [KB]-Seiten gefunden")
        return {"pages_found": 0, "enriched": 0, "skipped": 0, "errors": 0}

    # Nur echte [KB]-Seiten filtern
    kb_pages = [p for p in pages if p.get("title", "").startswith("[KB]")]
    log.info("topology_enricher: %d [KB]-Seiten gefunden", len(kb_pages))

    enriched = 0
    skipped = 0
    errors = 0

    for page in kb_pages:
        title = page.get("title", "")
        space_key = page.get("space_key", "002IT")
        service_name = _title_to_service(title)

        if not service_name or len(service_name) < 2:
            skipped += 1
            continue

        log.info("topology_enricher: analysiere '%s' (service=%s)", title, service_name)

        try:
            # RAG DeepSearch — lässt die it-aikb KI die Abhängigkeiten aus der
            # gesamten Dokumentation (nicht nur dieser Seite) synthetisieren
            rag = await conn.search_rag(
                query=(
                    f"Welche externen Services, Hosts, Datenbanken und Systemdienste "
                    f"benötigt {service_name}? "
                    f"Von welchen Services hängt {service_name} ab? "
                    f"Welche anderen Services sind von {service_name} abhängig? "
                    f"Liste alle Einträge aus der Abhängigkeits-Tabelle und dem "
                    f"Service-Abhängigkeiten-Panel auf."
                ),
                space_keys=["002IT", "MYC"],
                deepsearch=True,
            )

            answer = rag.get("answer", "")
            if not answer:
                log.debug("topology_enricher: keine RAG-Antwort für '%s'", title)
                skipped += 1
                continue

            service_deps = _extract_deps_from_text(answer)
            # Eigenen Service-Namen aus den Deps herausfiltern
            service_deps = [d for d in service_deps if d != service_name]

            # dependency_of: aus RAG-Antwort extrahieren
            # (Sätze wie "X hängt von {service} ab" oder "{service} wird genutzt von X")
            dep_of_section = ""
            dep_of_markers = [
                f"von {service_name} abhängig",
                f"{service_name} wird genutzt",
                f"nutzen {service_name}",
                f"verwendet {service_name}",
                "abhängig sind",
            ]
            for marker in dep_of_markers:
                pos = answer.lower().find(marker)
                if pos >= 0:
                    dep_of_section += " " + answer[max(0, pos - 50):pos + 200]
            dependency_of = _extract_deps_from_text(dep_of_section)
            dependency_of = [d for d in dependency_of if d != service_name and d not in service_deps]

            if not service_deps and not dependency_of:
                log.debug("topology_enricher: keine Deps erkannt für '%s'", title)
                skipped += 1
                continue

            # Zurückschreiben via it-aikb REST-API
            result = await conn.update_page_dependencies(
                page_title=title,
                space_key=space_key,
                service_dependencies=service_deps,
                dependency_of=dependency_of,
            )

            if result.get("updated"):
                log.info(
                    "topology_enricher: '%s' enriched → deps=%s, dep_of=%s",
                    title, service_deps, dependency_of,
                )
                enriched += 1
                # Auch in cs-knowledge speichern — schnellere lokale Abfrage bei Incidents
                try:
                    from app.services.knowledge_index import store_knowledge
                    await store_knowledge({
                        "kind": "dependency",
                        "service": service_name,
                        "title": f"{service_name} hängt ab von: {', '.join(service_deps)}",
                        "solution": (
                            f"Abhängigkeiten: {service_deps}. "
                            f"Benötigt von: {dependency_of}"
                        ),
                        "tags": [service_name] + service_deps + ["dependency"],
                        "source": "topology_enricher",
                        "confidence": 0.7,
                    })
                except Exception as _ke:
                    log.debug("topology_enricher: cs-knowledge write failed: %s", _ke)
            else:
                log.debug("topology_enricher: '%s' not updated: %s", title, result.get("message"))
                skipped += 1

        except Exception as exc:
            log.warning("topology_enricher: Fehler bei '%s': %s", title, exc)
            errors += 1

    log.info(
        "topology_enricher: fertig — pages=%d enriched=%d skipped=%d errors=%d",
        len(kb_pages), enriched, skipped, errors,
    )
    return {
        "pages_found": len(kb_pages),
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
    }
