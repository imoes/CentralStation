"""Skills REST-API — teamweite Skill-Bibliothek für Hermes.

Jeder Nutzer kann Skills anlegen (public oder private) und eigene Skills löschen.
Admins/Sysadmins können alle Skills bearbeiten und löschen.

GET    /api/skills              → alle öffentlichen + eigene private Skills
GET    /api/skills/{name}       → einzelner Skill (vollständig)
POST   /api/skills              → neuen Skill anlegen
PUT    /api/skills/{name}       → Skill aktualisieren (nur Ersteller oder Admin)
DELETE /api/skills/{name}       → Skill löschen (nur Ersteller oder Admin)
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/skills", tags=["skills"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class SkillCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9\-]{1,79}$",
                      description="Slug: Kleinbuchstaben + Bindestriche, z.B. 'graylog-restart'")
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=500,
                              description="1–2 Sätze: wann und wofür nutzen?")
    content: str = Field(..., min_length=10, description="Vollständige Anleitung in Markdown")
    tags: list[str] = Field(default_factory=list)
    version: str = Field(default="1.0", max_length=20)
    visibility: str = Field(default="public", pattern=r"^(public|private)$")


class SkillUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    version: str | None = None
    visibility: str | None = Field(default=None, pattern=r"^(public|private)$")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_admin(user) -> bool:
    return getattr(user, "role", "") in ("admin", "sysadmin")


# ── Routen ────────────────────────────────────────────────────────────────────

@router.get("")
async def get_skills(
    user: CurrentUser,
    tag: str = "",
) -> list[dict]:
    """Alle öffentlichen Skills + eigene private Skills."""
    from app.services.knowledge_index import list_skills
    return await list_skills(
        tag=tag,
        user_id=str(user.id),
        include_private=True,
    )


@router.get("/{name}")
async def get_skill_by_name(name: str, user: CurrentUser) -> dict:
    """Einzelnen Skill vollständig laden."""
    from app.services.knowledge_index import get_skill
    skill = await get_skill(name=name, user_id=str(user.id))
    if not skill:
        raise HTTPException(404, f"Skill '{name}' nicht gefunden")
    return skill


@router.post("", status_code=201)
async def create_skill(body: SkillCreate, user: CurrentUser) -> dict:
    """Neuen Skill anlegen. Jeder eingeloggte Nutzer darf Skills erstellen."""
    from app.services.knowledge_index import get_skill, store_skill
    # Name-Konflikt: öffentlich oder eigener privater Skill?
    existing = await get_skill(name=body.name, user_id=str(user.id))
    if existing:
        raise HTTPException(409, f"Skill '{body.name}' existiert bereits. Nutze PUT zum Aktualisieren.")
    result = await store_skill(
        name=body.name,
        title=body.title,
        description=body.description,
        content=body.content,
        tags=body.tags,
        version=body.version,
        author=getattr(user, "name", "") or getattr(user, "email", ""),
        user_id=str(user.id),
        visibility=body.visibility,
    )
    return result


@router.put("/{name}")
async def update_skill(name: str, body: SkillUpdate, user: CurrentUser) -> dict:
    """Skill aktualisieren. Nur Ersteller oder Admin."""
    from app.services.knowledge_index import get_skill, store_skill

    # Skill suchen (ohne User-Filter um Admins zu erlauben)
    skill = await get_skill(name=name, user_id=str(user.id))
    if not skill and _is_admin(user):
        # Admin-Fallback: Suche ohne User-Einschränkung
        skill = await get_skill(name=name, user_id="")

    if not skill:
        raise HTTPException(404, f"Skill '{name}' nicht gefunden")

    if not _is_admin(user) and skill.get("user_id") != str(user.id):
        raise HTTPException(403, "Nur der Ersteller oder ein Admin darf diesen Skill bearbeiten")

    result = await store_skill(
        name=name,
        title=body.title or skill["title"],
        description=body.description or skill["description"],
        content=body.content or skill["content"],
        tags=body.tags if body.tags is not None else skill.get("tags", []),
        version=body.version or skill.get("version", "1.0"),
        author=skill.get("author", ""),
        user_id=skill.get("user_id", str(user.id)),
        visibility=body.visibility or skill.get("visibility", "public"),
    )
    return result


@router.delete("/{name}")
async def delete_skill_route(name: str, user: CurrentUser) -> dict:
    """Skill löschen. Nur Ersteller oder Admin."""
    from app.services.knowledge_index import delete_skill
    ok = await delete_skill(
        name=name,
        user_id=str(user.id),
        is_admin=_is_admin(user),
    )
    if not ok:
        raise HTTPException(404, f"Skill '{name}' nicht gefunden oder keine Berechtigung")
    return {"deleted": True, "name": name}
