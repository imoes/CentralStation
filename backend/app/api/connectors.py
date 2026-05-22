import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin
from app.core.database import get_db
from app.core.security import decrypt_credentials, encrypt_credentials
from app.models.audit import AuditLog
from app.models.connector import ConnectorConfig
from app.schemas.connector import (
    ConnectorCreate, ConnectorResponse, ConnectorTestResult, ConnectorUpdate,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])

VALID_TYPES = {
    "checkmk", "graylog", "wazuh", "jira", "jira_sd",
    "o365", "prometheus", "netbox", "id_generator", "it_aikb",
}  # keep in sync with get_connector() factory


@router.get("/", response_model=list[ConnectorResponse], dependencies=[RequireAdmin])
async def list_connectors(db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(ConnectorConfig).order_by(ConnectorConfig.type, ConnectorConfig.name)
    )
    return result.scalars().all()


@router.post("/", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[RequireAdmin])
async def create_connector(
    data: ConnectorCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    if data.type not in VALID_TYPES:
        raise HTTPException(400, f"Unknown connector type. Valid: {VALID_TYPES}")

    connector = ConnectorConfig(
        name=data.name,
        type=data.type,
        base_url=data.base_url,
        encrypted_credentials=encrypt_credentials(data.credentials),
        enabled=data.enabled,
        created_by=current_user.id,
    )
    db.add(connector)
    db.add(AuditLog(action="connector_created", resource_type="connector",
                    resource_id=data.name, user_id=current_user.id,
                    new_value={"type": data.type, "name": data.name}))
    await db.commit()
    await db.refresh(connector)
    return connector


@router.patch("/{connector_id}", response_model=ConnectorResponse,
              dependencies=[RequireAdmin])
async def update_connector(
    connector_id: uuid.UUID,
    data: ConnectorUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")

    if data.name is not None:
        connector.name = data.name
    if data.base_url is not None:
        connector.base_url = data.base_url
    if data.credentials is not None:
        connector.encrypted_credentials = encrypt_credentials(data.credentials)
    if data.enabled is not None:
        connector.enabled = data.enabled

    db.add(AuditLog(action="connector_updated", resource_type="connector",
                    resource_id=str(connector_id), user_id=current_user.id))
    await db.commit()
    await db.refresh(connector)
    return connector


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[RequireAdmin])
async def delete_connector(
    connector_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")

    await db.delete(connector)
    db.add(AuditLog(action="connector_deleted", resource_type="connector",
                    resource_id=str(connector_id), user_id=current_user.id))
    await db.commit()


@router.post("/{connector_id}/test", response_model=ConnectorTestResult,
             dependencies=[RequireAdmin])
async def test_connector(
    connector_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")

    credentials = decrypt_credentials(connector.encrypted_credentials)

    # Import connector factory lazily
    from app.services.connectors import get_connector
    svc = get_connector(connector.type, connector.base_url, credentials)
    return await svc.test_connection()
