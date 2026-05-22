import uuid
from datetime import datetime

from pydantic import BaseModel


class KanbanCardCreate(BaseModel):
    title: str
    description: str | None = None
    status: str = "backlog"
    priority: str = "medium"
    assigned_to: uuid.UUID | None = None
    alert_id: uuid.UUID | None = None


class KanbanCardUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to: uuid.UUID | None = None
    position: int | None = None


class KanbanCardMove(BaseModel):
    status: str
    position: int


class KanbanCardResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: str
    priority: str
    jira_key: str | None
    assigned_to: uuid.UUID | None
    alert_id: uuid.UUID | None
    ai_generated: bool
    position: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
