import uuid
from datetime import datetime

from pydantic import BaseModel


class AlertResponse(BaseModel):
    id: uuid.UUID
    source: str
    severity: str
    title: str
    body: str | None
    external_id: str | None
    status: str
    metadata_: dict | None = None
    location_name: str | None
    location_city: str | None
    acknowledged_by: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class AlertAcknowledge(BaseModel):
    alert_id: uuid.UUID


class AlertFilter(BaseModel):
    source: str | None = None
    severity: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0
