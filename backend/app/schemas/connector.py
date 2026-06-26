import uuid
from datetime import datetime

from pydantic import BaseModel


class ConnectorCreate(BaseModel):
    name: str
    type: str
    base_url: str | None = None
    credentials: dict  # plain dict — wird vor dem Speichern verschlüsselt
    enabled: bool = True


class ConnectorUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    credentials: dict | None = None
    enabled: bool | None = None


class ConnectorResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    base_url: str | None
    enabled: bool
    owner_user_id: uuid.UUID | None = None
    updated_at: datetime
    # Credentials werden NIEMALS zurückgegeben

    model_config = {"from_attributes": True}


class ConnectorTestResult(BaseModel):
    success: bool
    message: str
    details: dict | None = None
