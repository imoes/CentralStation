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
    "checkmk", "graylog", "wazuh", "icinga2", "jira", "jira_sd",
    "o365", "teams", "prometheus", "netbox", "id_generator", "coroot",
    "aikb", "smtp",
}  # keep in sync with get_connector() factory
USER_MANAGED_TYPES = {"o365", "teams", "jira", "jira_sd"}


def _is_admin(user) -> bool:
    return user.role == "admin"


async def _get_connector_or_404(db: AsyncSession, connector_id: uuid.UUID) -> ConnectorConfig:
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")
    return connector


def _assert_can_manage_personal(connector_type: str) -> None:
    if connector_type not in USER_MANAGED_TYPES:
        raise HTTPException(403, f"Connector type '{connector_type}' ist nur global durch Admins konfigurierbar")


def _assert_access(connector: ConnectorConfig, user) -> None:
    if _is_admin(user):
        return
    if connector.owner_user_id != user.id:
        raise HTTPException(403, "Kein Zugriff auf diesen Connector")


@router.get("/my", response_model=list[ConnectorResponse])
async def list_my_connectors(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.owner_user_id == current_user.id,
            ConnectorConfig.type.in_(USER_MANAGED_TYPES),
        )
        .order_by(ConnectorConfig.type, ConnectorConfig.name)
    )
    return result.scalars().all()


@router.put("/my/{connector_type}", response_model=ConnectorResponse)
async def upsert_my_connector(
    connector_type: str,
    data: ConnectorCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    if connector_type not in VALID_TYPES or data.type != connector_type:
        raise HTTPException(400, "Connector-Typ im Pfad und Body muss übereinstimmen")
    _assert_can_manage_personal(connector_type)

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.owner_user_id == current_user.id,
            ConnectorConfig.type == connector_type,
        )
    )
    connector = result.scalar_one_or_none()
    if connector:
        existing_credentials = decrypt_credentials(connector.encrypted_credentials)
        merged_credentials = {**existing_credentials, **data.credentials}
        connector.name = data.name
        connector.base_url = data.base_url
        connector.encrypted_credentials = encrypt_credentials(merged_credentials)
        connector.enabled = data.enabled
        action = "connector_updated"
    else:
        connector = ConnectorConfig(
            name=data.name,
            type=data.type,
            base_url=data.base_url,
            encrypted_credentials=encrypt_credentials(data.credentials),
            enabled=data.enabled,
            created_by=current_user.id,
            owner_user_id=current_user.id,
        )
        db.add(connector)
        action = "connector_created"

    db.add(AuditLog(
        action=action,
        resource_type="connector",
        resource_id=f"{connector_type}:{current_user.id}",
        user_id=current_user.id,
        new_value={"type": data.type, "name": data.name, "scope": "personal"},
    ))
    await db.commit()
    await db.refresh(connector)
    return connector


@router.delete("/my/{connector_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_connector(
    connector_type: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    _assert_can_manage_personal(connector_type)
    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.owner_user_id == current_user.id,
            ConnectorConfig.type == connector_type,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Persönlicher Connector nicht gefunden")

    await db.delete(connector)
    db.add(AuditLog(
        action="connector_deleted",
        resource_type="connector",
        resource_id=f"{connector_type}:{current_user.id}",
        user_id=current_user.id,
    ))
    await db.commit()


@router.post("/my/{connector_type}/test", response_model=ConnectorTestResult)
async def test_my_connector(
    connector_type: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    _assert_can_manage_personal(connector_type)
    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.owner_user_id == current_user.id,
            ConnectorConfig.type == connector_type,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Persönlicher Connector nicht gefunden")

    credentials = decrypt_credentials(connector.encrypted_credentials)
    from app.services.connectors import get_connector
    svc = get_connector(connector.type, connector.base_url, credentials)
    return await svc.test_connection()


@router.get("/", response_model=list[ConnectorResponse], dependencies=[RequireAdmin])
async def list_connectors(db: Annotated[AsyncSession, Depends(get_db)]):
    # Only global connectors (owner_user_id IS NULL). Personal per-user connectors
    # live under /connectors/my and are never shown in the admin global list.
    result = await db.execute(
        select(ConnectorConfig)
        .where(ConnectorConfig.owner_user_id.is_(None))
        .order_by(ConnectorConfig.type, ConnectorConfig.name)
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
        owner_user_id=None,
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
    connector = await _get_connector_or_404(db, connector_id)
    _assert_access(connector, current_user)

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
    connector = await _get_connector_or_404(db, connector_id)
    _assert_access(connector, current_user)

    await db.delete(connector)
    db.add(AuditLog(action="connector_deleted", resource_type="connector",
                    resource_id=str(connector_id), user_id=current_user.id))
    await db.commit()


@router.post("/{connector_id}/test", response_model=ConnectorTestResult,
             dependencies=[RequireAdmin])
async def test_connector(
    connector_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    connector = await _get_connector_or_404(db, connector_id)
    _assert_access(connector, current_user)

    credentials = decrypt_credentials(connector.encrypted_credentials)

    from app.services.connectors import get_connector
    svc = get_connector(connector.type, connector.base_url, credentials)
    return await svc.test_connection()


# ── Microsoft Device Code Flow ────────────────────────────────────────────────

@router.post("/{connector_id}/ms-device-code")
async def ms_device_code_start(
    connector_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    """Start a Microsoft Device Code flow for an O365 or Teams connector.

    Returns user_code + verification_url for the user to open in a browser,
    plus device_code for the client to poll /ms-device-code/complete.
    """
    connector = await _get_connector_or_404(db, connector_id)
    _assert_access(connector, current_user)

    if connector.type not in ("o365", "teams"):
        raise HTTPException(400, "Nur für O365/Teams-Connectoren verfügbar")

    credentials = decrypt_credentials(connector.encrypted_credentials)
    tenant_id = credentials.get("tenant_id", "")
    client_id = credentials.get("client_id", "")
    if not tenant_id or not client_id:
        raise HTTPException(400, "tenant_id und client_id müssen gespeichert sein")

    scopes = (
        "Mail.Read Mail.Send Calendars.ReadWrite offline_access"
        if connector.type == "o365"
        else "ChannelMessage.Read.All Team.ReadBasic.All offline_access"
    )

    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode",
            data={"client_id": client_id, "scope": scopes},
        )
        if r.status_code != 200:
            raise HTTPException(502, f"Microsoft-Fehler: {r.text}")
        data = r.json()

    return {
        "user_code": data["user_code"],
        "verification_url": data.get("verification_uri", "https://microsoft.com/devicelogin"),
        "device_code": data["device_code"],
        "expires_in": data.get("expires_in", 900),
        "interval": data.get("interval", 5),
        "message": data.get("message", ""),
    }


@router.post("/{connector_id}/ms-device-code/complete")
async def ms_device_code_complete(
    connector_id: uuid.UUID,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    """Poll once for the Device Code token. If authorized, saves refresh_token.

    body: { device_code: str }
    Returns: { status: 'authorized'|'pending'|'error', message?: str }
    """
    connector = await _get_connector_or_404(db, connector_id)
    _assert_access(connector, current_user)

    credentials = decrypt_credentials(connector.encrypted_credentials)
    tenant_id = credentials.get("tenant_id", "")
    client_id = credentials.get("client_id", "")
    client_secret = credentials.get("client_secret", "")
    device_code = body.get("device_code", "")

    if not device_code:
        raise HTTPException(400, "device_code fehlt")

    import httpx
    token_data: dict = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": client_id,
        "device_code": device_code,
    }
    if client_secret:
        token_data["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data=token_data,
        )
        resp = r.json()

    error = resp.get("error", "")
    if error == "authorization_pending":
        return {"status": "pending", "message": "Warte auf Benutzer-Bestätigung…"}
    if error == "expired_token":
        return {"status": "error", "message": "Code abgelaufen. Bitte erneut starten."}
    if error:
        return {"status": "error", "message": resp.get("error_description", error)}

    refresh_token = resp.get("refresh_token", "")
    if not refresh_token:
        return {"status": "error", "message": "Kein Refresh-Token erhalten"}

    # Persist refresh_token in connector credentials
    credentials["refresh_token"] = refresh_token
    connector.encrypted_credentials = encrypt_credentials(credentials)
    db.add(AuditLog(
        action="connector_ms_authorized",
        resource_type="connector",
        resource_id=str(connector_id),
        user_id=current_user.id,
    ))
    await db.commit()

    return {"status": "authorized", "message": "Microsoft-Konto erfolgreich verbunden"}


# ── Coroot project discovery ──────────────────────────────────────────────────

from pydantic import BaseModel as _BaseModel


class _CorootDiscoverBody(_BaseModel):
    base_url: str
    email: str
    password: str


@router.post("/coroot/projects", dependencies=[RequireAdmin])
async def discover_coroot_projects(body: _CorootDiscoverBody) -> list[dict]:
    """Return available Coroot projects for the project-selector dropdown.

    Called by the frontend connector form when the user clicks 'Projekte laden'.
    """
    from app.services.connectors.coroot import CorootConnector
    svc = CorootConnector(
        base_url=body.base_url,
        credentials={"email": body.email, "password": body.password},
    )
    try:
        projects = await svc.list_projects()
        return projects  # [{id, name}, ...]
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(502, f"Coroot nicht erreichbar: {exc}")
