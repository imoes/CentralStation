"""AWX-NG REST API proxy — forwards management requests to the user's configured
awx_ng connector. Used by the Maschinenraum (/engineering) frontend.

Only management endpoints are exposed here (hosts, inventories, job templates,
jobs). Admin setup (credentials, execution nodes) is done directly in AWX-NG.
"""
from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.core.security import decrypt_credentials
from app.models.connector import ConnectorConfig

router = APIRouter(prefix="/awx-ng", tags=["awx-ng"])
log = logging.getLogger(__name__)

_TIMEOUT = 30.0


async def _get_connector(user, db: AsyncSession) -> ConnectorConfig:
    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "awx_ng",
            ConnectorConfig.owner_user_id == user.id,
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Kein AWX-NG Konnektor konfiguriert")
    return conn


async def _awx_get(path: str, conn: ConnectorConfig, params: dict | None = None) -> dict:
    creds = decrypt_credentials(conn.encrypted_credentials)
    base = (conn.base_url or "").rstrip("/")
    url = f"{base}{path}"
    auth = (creds.get("username", ""), creds.get("password", ""))
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, auth=auth, params=params or {},
                                 headers={"Content-Type": "application/json"})
        if r.status_code == 401:
            raise HTTPException(401, "AWX-NG: Ungültige Zugangsdaten")
        if r.status_code >= 400:
            raise HTTPException(502, f"AWX-NG Fehler {r.status_code}: {r.text[:200]}")
        return r.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "AWX-NG: Timeout")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"AWX-NG nicht erreichbar: {exc}")


async def _awx_post(path: str, conn: ConnectorConfig, payload: dict) -> dict:
    creds = decrypt_credentials(conn.encrypted_credentials)
    base = (conn.base_url or "").rstrip("/")
    url = f"{base}{path}"
    auth = (creds.get("username", ""), creds.get("password", ""))
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, auth=auth, json=payload,
                                  headers={"Content-Type": "application/json"})
        if r.status_code == 401:
            raise HTTPException(401, "AWX-NG: Ungültige Zugangsdaten")
        if r.status_code >= 400:
            raise HTTPException(502, f"AWX-NG Fehler {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}
    except httpx.TimeoutException:
        raise HTTPException(504, "AWX-NG: Timeout")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"AWX-NG nicht erreichbar: {exc}")


@router.get("/hosts")
async def list_hosts(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    page_size: int = 50,
    search: str = "",
):
    conn = await _get_connector(user, db)
    params: dict = {"page": page, "page_size": page_size, "order_by": "name"}
    if search:
        params["name__icontains"] = search
    data = await _awx_get("/api/v2/hosts/", conn, params)
    return {
        "count": data.get("count", 0),
        "results": [
            {
                "id": h["id"],
                "name": h["name"],
                "description": h.get("description", ""),
                "enabled": h.get("enabled", True),
                "inventory": h.get("summary_fields", {}).get("inventory", {}).get("name", ""),
                "last_job": h.get("summary_fields", {}).get("last_job", {}),
                "variables": h.get("variables", ""),
            }
            for h in data.get("results", [])
        ],
    }


@router.get("/inventories")
async def list_inventories(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conn = await _get_connector(user, db)
    data = await _awx_get("/api/v2/inventories/", conn, {"page_size": 100, "order_by": "name"})
    return {
        "count": data.get("count", 0),
        "results": [
            {
                "id": inv["id"],
                "name": inv["name"],
                "description": inv.get("description", ""),
                "total_hosts": inv.get("total_hosts", 0),
                "hosts_with_active_failures": inv.get("hosts_with_active_failures", 0),
            }
            for inv in data.get("results", [])
        ],
    }


@router.get("/job-templates")
async def list_job_templates(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conn = await _get_connector(user, db)
    data = await _awx_get("/api/v2/job_templates/", conn, {"page_size": 100, "order_by": "name"})
    return {
        "count": data.get("count", 0),
        "results": [
            {
                "id": jt["id"],
                "name": jt["name"],
                "description": jt.get("description", ""),
                "playbook": jt.get("playbook", ""),
                "inventory": jt.get("summary_fields", {}).get("inventory", {}).get("name", ""),
                "ask_variables_on_launch": jt.get("ask_variables_on_launch", False),
            }
            for jt in data.get("results", [])
        ],
    }


@router.get("/jobs")
async def list_jobs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
):
    conn = await _get_connector(user, db)
    data = await _awx_get("/api/v2/jobs/", conn, {"page_size": limit, "order_by": "-started"})
    return {
        "count": data.get("count", 0),
        "results": [
            {
                "id": j["id"],
                "name": j.get("name", ""),
                "status": j.get("status", ""),
                "started": j.get("started"),
                "finished": j.get("finished"),
                "elapsed": j.get("elapsed"),
                "failed": j.get("failed", False),
                "job_template": j.get("summary_fields", {}).get("job_template", {}).get("name", ""),
                "launched_by": j.get("summary_fields", {}).get("launched_by", {}).get("username", ""),
                "limit": j.get("limit", ""),
            }
            for j in data.get("results", [])
        ],
    }


@router.get("/jobs/{job_id}/output")
async def job_output(
    job_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conn = await _get_connector(user, db)
    data = await _awx_get(f"/api/v2/jobs/{job_id}/stdout/", conn, {"format": "json"})
    return {"content": data.get("content", "")}


@router.post("/job-templates/{template_id}/launch")
async def launch_job(
    template_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: dict = {},
):
    conn = await _get_connector(user, db)
    data = await _awx_post(f"/api/v2/job_templates/{template_id}/launch/", conn, body)
    return {"job_id": data.get("id"), "status": data.get("status", "pending")}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conn = await _get_connector(user, db)
    await _awx_post(f"/api/v2/jobs/{job_id}/cancel/", conn, {})
    return {"ok": True}
