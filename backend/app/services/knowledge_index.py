"""Living Documentation — cs-knowledge und cs-skills OpenSearch Indizes.

cs-knowledge: Teamweite Erkenntnisse, gelöste Probleme, Service-Abhängigkeiten.
              Wird von Hermes (via MCP), dem Topology-Enricher und dem Computer-
              Session-Resolver befüllt.

cs-skills:    Teamweite, wiederverwendbare Prozeduren für Hermes.
              Wird via MCP (store_skill) und REST-API (/api/skills) verwaltet.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.opensearch import get_opensearch

log = logging.getLogger(__name__)

# ── Index-Namen ────────────────────────────────────────────────────────────────

CS_KNOWLEDGE_INDEX = "cs-knowledge"
CS_SKILLS_INDEX = "cs-skills"

# ── Mappings ───────────────────────────────────────────────────────────────────

_KNOWLEDGE_MAPPING = {
    "mappings": {
        "properties": {
            "kind":        {"type": "keyword"},   # lesson|dependency|pattern|runbook
            "service":     {"type": "keyword"},
            "host":        {"type": "keyword"},
            "title":       {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "problem":     {"type": "text"},
            "solution":    {"type": "text"},
            "tags":        {"type": "keyword"},
            "confidence":  {"type": "float"},
            "source":      {"type": "keyword"},   # hermes|topology_enricher|computer_session
            "session_id":  {"type": "keyword"},
            "created_at":  {"type": "date"},
            "updated_at":  {"type": "date"},
            "vote_score":  {"type": "integer"},
        }
    },
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
}

_SKILLS_MAPPING = {
    "mappings": {
        "properties": {
            "name":        {"type": "keyword"},
            "title":       {"type": "text", "fields": {"raw": {"type": "keyword"}}},
            "description": {"type": "text"},
            "content":     {"type": "text"},
            "tags":        {"type": "keyword"},
            "author":      {"type": "keyword"},
            "version":     {"type": "keyword"},
            "enabled":     {"type": "boolean"},
            "visibility":  {"type": "keyword"},   # "public"|"private"
            "user_id":     {"type": "keyword"},   # Ersteller (leer = system/hermes)
            "created_at":  {"type": "date"},
            "updated_at":  {"type": "date"},
        }
    },
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
}


# ── Index-Initialisierung ──────────────────────────────────────────────────────

async def ensure_knowledge_indices() -> None:
    """Erstellt cs-knowledge und cs-skills falls nicht vorhanden."""
    os_client = get_opensearch()
    for index, mapping in (
        (CS_KNOWLEDGE_INDEX, _KNOWLEDGE_MAPPING),
        (CS_SKILLS_INDEX, _SKILLS_MAPPING),
    ):
        try:
            exists = await os_client.indices.exists(index=index)
            if not exists:
                await os_client.indices.create(index=index, body=mapping)
                log.info("Created OpenSearch index: %s", index)
        except Exception as exc:
            log.warning("Could not create index %s: %s", index, exc)


# ── cs-knowledge CRUD ─────────────────────────────────────────────────────────

async def store_knowledge(doc: dict) -> str:
    """Speichert eine Erkenntnis in cs-knowledge. Gibt die Doc-ID zurück."""
    os_client = get_opensearch()
    now = datetime.now(timezone.utc).isoformat()
    doc_id = str(uuid.uuid4())
    body = {
        "kind":       doc.get("kind", "lesson"),
        "service":    doc.get("service", ""),
        "host":       doc.get("host", ""),
        "title":      doc.get("title", ""),
        "problem":    doc.get("problem", ""),
        "solution":   doc.get("solution", ""),
        "tags":       doc.get("tags", []),
        "confidence": float(doc.get("confidence", 0.8)),
        "source":     doc.get("source", "hermes"),
        "session_id": doc.get("session_id", ""),
        "created_at": now,
        "updated_at": now,
        "vote_score": 0,
    }
    try:
        await os_client.index(index=CS_KNOWLEDGE_INDEX, id=doc_id, body=body)
        log.info("knowledge_index: stored %s '%s'", body["kind"], body["title"][:60])
    except Exception as exc:
        log.warning("knowledge_index: store_knowledge failed: %s", exc)
    return doc_id


async def search_knowledge(
    query: str,
    kind: str | None = None,
    service: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Volltext-Suche in cs-knowledge mit optionalen Keyword-Filtern."""
    os_client = get_opensearch()

    must: list[dict] = []
    if query.strip():
        must.append({
            "multi_match": {
                "query": query,
                "fields": ["title^2", "problem", "solution", "service^1.5"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        })

    filters: list[dict] = []
    if kind:
        filters.append({"term": {"kind": kind}})
    if service:
        filters.append({"term": {"service": service}})
    if tags:
        filters.append({"terms": {"tags": tags}})

    body: dict[str, Any] = {
        "query": {
            "bool": {
                "must": must or [{"match_all": {}}],
                "filter": filters,
            }
        },
        "sort": [{"confidence": {"order": "desc"}}, {"_score": {"order": "desc"}}],
        "size": limit,
    }

    try:
        resp = await os_client.search(index=CS_KNOWLEDGE_INDEX, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        return [{"id": h["_id"], **h["_source"]} for h in hits]
    except Exception as exc:
        log.warning("knowledge_index: search_knowledge failed: %s", exc)
        return []


# ── cs-skills CRUD ────────────────────────────────────────────────────────────

async def list_skills(
    tag: str = "",
    user_id: str = "",
    include_private: bool = False,
) -> list[dict]:
    """Gibt Skills zurück.

    Öffentliche Skills: immer sichtbar.
    Private Skills: nur wenn include_private=True UND user_id übereinstimmt.
    """
    os_client = get_opensearch()
    # Öffentliche Skills für alle + eigene private Skills
    visibility_filter: dict
    if include_private and user_id:
        visibility_filter = {
            "bool": {
                "should": [
                    {"term": {"visibility": "public"}},
                    {"bool": {"filter": [
                        {"term": {"visibility": "private"}},
                        {"term": {"user_id": user_id}},
                    ]}},
                ],
                "minimum_should_match": 1,
            }
        }
    else:
        visibility_filter = {"term": {"visibility": "public"}}

    filters: list[dict] = [{"term": {"enabled": True}}, visibility_filter]
    if tag:
        filters.append({"term": {"tags": tag}})

    body = {
        "query": {"bool": {"filter": filters}},
        "sort": [{"name": {"order": "asc"}}],
        "size": 200,
        "_source": ["name", "title", "description", "tags", "version", "author",
                    "user_id", "visibility", "updated_at"],
    }
    try:
        resp = await os_client.search(index=CS_SKILLS_INDEX, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        return [{"id": h["_id"], **h["_source"]} for h in hits]
    except Exception as exc:
        log.warning("knowledge_index: list_skills failed: %s", exc)
        return []


async def get_skill(name: str, user_id: str = "") -> dict | None:
    """Lädt einen Skill anhand seines Namens.

    Private Skills werden nur zurückgegeben wenn user_id übereinstimmt.
    """
    os_client = get_opensearch()
    should = [
        {"term": {"visibility": "public"}},
    ]
    if user_id:
        should.append({"bool": {"filter": [
            {"term": {"visibility": "private"}},
            {"term": {"user_id": user_id}},
        ]}})
    body = {
        "query": {"bool": {"filter": [
            {"term": {"name": name}},
            {"term": {"enabled": True}},
            {"bool": {"should": should, "minimum_should_match": 1}},
        ]}},
        "size": 1,
    }
    try:
        resp = await os_client.search(index=CS_SKILLS_INDEX, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        if hits:
            return {"id": hits[0]["_id"], **hits[0]["_source"]}
    except Exception as exc:
        log.warning("knowledge_index: get_skill failed: %s", exc)
    return None


async def store_skill(
    name: str,
    title: str,
    description: str,
    content: str,
    tags: list[str] | None = None,
    version: str = "1.0",
    author: str = "hermes",
    user_id: str = "",
    visibility: str = "public",
) -> dict:
    """Erstellt oder aktualisiert einen Skill (upsert by name + user_id).

    Nur der Ersteller (user_id) kann seinen eigenen Skill aktualisieren.
    Admins können alle Skills aktualisieren (kein user_id-Check hier — REST-Layer prüft).
    """
    os_client = get_opensearch()
    now = datetime.now(timezone.utc).isoformat()

    existing = await get_skill(name, user_id=user_id)
    body = {
        "name":        name,
        "title":       title,
        "description": description,
        "content":     content,
        "tags":        tags or [],
        "version":     version,
        "author":      author,
        "user_id":     user_id,
        "visibility":  visibility if visibility in ("public", "private") else "public",
        "enabled":     True,
        "updated_at":  now,
    }

    try:
        if existing:
            doc_id = existing["id"]
            await os_client.update(
                index=CS_SKILLS_INDEX, id=doc_id,
                body={"doc": {**body}},
            )
            log.info("knowledge_index: updated skill '%s' v%s", name, version)
            return {"updated": True, "id": doc_id, "name": name}
        else:
            body["created_at"] = now
            doc_id = str(uuid.uuid4())
            await os_client.index(index=CS_SKILLS_INDEX, id=doc_id, body=body)
            log.info("knowledge_index: created skill '%s' v%s", name, version)
            return {"updated": False, "id": doc_id, "name": name}
    except Exception as exc:
        log.warning("knowledge_index: store_skill failed: %s", exc)
        return {"updated": False, "id": "", "name": name, "error": str(exc)}


async def delete_skill(name: str, user_id: str = "", is_admin: bool = False) -> bool:
    """Deaktiviert einen Skill (soft-delete).

    Nutzer können nur ihre eigenen Skills löschen.
    Admins können alle löschen (is_admin=True).
    """
    os_client = get_opensearch()
    # Admin darf alles sehen
    existing = await get_skill(name, user_id=user_id if not is_admin else "")
    if not existing and is_admin:
        # Admin-Fallback: Skill ohne User-Filter suchen
        body = {
            "query": {"bool": {"filter": [
                {"term": {"name": name}}, {"term": {"enabled": True}},
            ]}},
            "size": 1,
        }
        try:
            resp = await os_client.search(index=CS_SKILLS_INDEX, body=body)
            hits = resp.get("hits", {}).get("hits", [])
            if hits:
                existing = {"id": hits[0]["_id"], **hits[0]["_source"]}
        except Exception:
            pass

    if not existing:
        return False

    # Prüfen ob User berechtigt ist
    if not is_admin and existing.get("user_id") != user_id:
        return False

    try:
        await os_client.update(
            index=CS_SKILLS_INDEX, id=existing["id"],
            body={"doc": {"enabled": False, "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        return True
    except Exception as exc:
        log.warning("knowledge_index: delete_skill failed: %s", exc)
        return False
