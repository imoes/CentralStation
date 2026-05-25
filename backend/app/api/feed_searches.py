"""Feed Searches — saved OpenSearch queries for Feed and Dashboard widgets."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin, get_db
from app.models.workflow import FeedSearch
from app.services import feed_index

router = APIRouter(prefix="/feed-searches", tags=["feed-searches"])


class FeedSearchCreate(BaseModel):
    name: str
    index_pattern: str = "cs-feed-*"
    query_string: str = ""
    enabled: bool = True


class FeedSearchUpdate(BaseModel):
    name: str | None = None
    index_pattern: str | None = None
    query_string: str | None = None
    enabled: bool | None = None
    position: int | None = None


def _to_dict(s: FeedSearch) -> dict:
    return {
        "id": str(s.id),
        "user_id": str(s.user_id) if s.user_id else None,
        "name": s.name,
        "index_pattern": s.index_pattern,
        "query_string": s.query_string,
        "enabled": s.enabled,
        "is_system": s.is_system,
        "position": s.position,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/")
async def list_searches(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return system searches + personal searches of the current user."""
    result = await db.execute(
        select(FeedSearch)
        .where(
            (FeedSearch.user_id == None) |  # noqa: E711
            (FeedSearch.user_id == current_user.id)
        )
        .order_by(FeedSearch.is_system.desc(), FeedSearch.position, FeedSearch.created_at)
    )
    searches = result.scalars().all()
    return [_to_dict(s) for s in searches]


@router.post("/", status_code=201)
async def create_search(
    body: FeedSearchCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a personal feed search for the current user."""
    s = FeedSearch(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=body.name,
        index_pattern=body.index_pattern,
        query_string=body.query_string,
        enabled=body.enabled,
        is_system=False,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _to_dict(s)


@router.post("/system", status_code=201, dependencies=[RequireAdmin])
async def create_system_search(
    body: FeedSearchCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Admin: create a system-wide feed search (user_id=NULL, is_system=True)."""
    s = FeedSearch(
        id=uuid.uuid4(),
        user_id=None,
        name=body.name,
        index_pattern=body.index_pattern,
        query_string=body.query_string,
        enabled=body.enabled,
        is_system=True,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _to_dict(s)


@router.patch("/{search_id}")
async def update_search(
    search_id: uuid.UUID,
    body: FeedSearchUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(FeedSearch).where(FeedSearch.id == search_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Search not found")

    is_admin = current_user.role in ("admin", "sysadmin")

    # Personal search — owner can edit all fields
    if s.user_id and s.user_id == current_user.id:
        for field, val in body.model_dump(exclude_none=True).items():
            setattr(s, field, val)
    # System search — only admins edit the shared row. User-specific disabling is
    # stored in user_preferences.feed_disabled_search_ids.
    elif s.is_system:
        if is_admin:
            for field, val in body.model_dump(exclude_none=True).items():
                setattr(s, field, val)
        else:
            raise HTTPException(403, "Only admins can edit system searches")
    else:
        raise HTTPException(403, "Not your search")

    await db.commit()
    await db.refresh(s)
    return _to_dict(s)


@router.delete("/{search_id}", status_code=204)
async def delete_search(
    search_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(FeedSearch).where(FeedSearch.id == search_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Search not found")
    if s.is_system:
        raise HTTPException(403, "System searches cannot be deleted")
    if s.user_id != current_user.id:
        raise HTTPException(403, "Not your search")
    await db.delete(s)
    await db.commit()


@router.get("/{search_id}/preview")
async def preview_search(
    search_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    size: int = 5,
):
    """Return up to `size` matching items for a search (for UI preview)."""
    result = await db.execute(select(FeedSearch).where(FeedSearch.id == search_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Search not found")
    if s.user_id and s.user_id != current_user.id:
        raise HTTPException(403, "Not your search")

    items = await feed_index.search_by_query(
        index_pattern=s.index_pattern,
        query_string=s.query_string,
        size=min(size, 20),
        user_id=str(current_user.id),
    )
    return {"items": items, "count": len(items)}
