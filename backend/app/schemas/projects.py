import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

VALID_PROJECT_STATUSES = {"planning", "active", "done", "archived"}
VALID_STEP_STATUSES = {"pending", "in_progress", "done"}
VALID_ISSUE_TYPES = {"epic", "story", "task", "subtask", "bug"}
VALID_PRIORITIES = {"highest", "high", "medium", "low", "lowest"}


# ── Project ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    status: str = "planning"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: str
    owner_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ── Step ─────────────────────────────────────────────────────────────────────

class StepCreate(BaseModel):
    title: str
    description: str | None = None
    status: str = "pending"
    jira_issue_type: str = "task"
    priority: str = "medium"
    duration_days: int = 1
    story_points: int | None = None
    sort_order: int = 0
    parent_step_id: uuid.UUID | None = None
    depends_on: list[uuid.UUID] = []
    assignee: str | None = None
    labels: list[str] = []
    due_date: date | None = None
    acceptance_criteria: str | None = None
    pos_x: int | None = None
    pos_y: int | None = None


class StepUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    jira_issue_type: str | None = None
    priority: str | None = None
    duration_days: int | None = None
    story_points: int | None = None
    sort_order: int | None = None
    parent_step_id: uuid.UUID | None = None
    assignee: str | None = None
    labels: list[str] | None = None
    due_date: date | None = None
    acceptance_criteria: str | None = None
    pos_x: int | None = None
    pos_y: int | None = None


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    parent_step_id: uuid.UUID | None
    title: str
    description: str | None
    status: str
    jira_issue_type: str
    priority: str
    duration_days: int
    story_points: int | None
    sort_order: int
    assignee: str | None
    labels: str | None              # stored as JSON string in DB
    due_date: date | None
    acceptance_criteria: str | None
    est_start: int | None
    est_end: int | None
    lst_start: int | None
    lst_end: int | None
    slack: int | None
    pos_x: int | None
    pos_y: int | None
    jira_connector_type: str | None
    jira_key: str | None
    jira_issue_id: str | None
    jira_status: str | None
    jira_status_category: str | None
    jira_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ── Dependency ───────────────────────────────────────────────────────────────

class DepCreate(BaseModel):
    step_id: uuid.UUID
    depends_on_step_id: uuid.UUID


class DepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_id: uuid.UUID
    depends_on_step_id: uuid.UUID


# ── Jira attachment ──────────────────────────────────────────────────────────

class AttachTicketRequest(BaseModel):
    connector_type: str   # jira | jira_sd
    jira_key: str


class CreateTicketRequest(BaseModel):
    connector_type: str
    summary: str | None = None
    description: str | None = None
    issue_type: str = "Task"
    epic_key: str | None = None


# ── Plan graph (the rich read model) ─────────────────────────────────────────

class StepNode(BaseModel):
    """A step as returned in the project graph — includes CPM + card fields + Jira."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_step_id: uuid.UUID | None
    title: str
    description: str | None
    status: str
    jira_issue_type: str
    priority: str
    duration_days: int
    story_points: int | None
    sort_order: int
    assignee: str | None
    labels: str | None          # JSON string
    due_date: date | None
    acceptance_criteria: str | None
    est_start: int | None
    est_end: int | None
    lst_start: int | None
    lst_end: int | None
    slack: int | None
    critical: bool = False
    pos_x: int | None
    pos_y: int | None
    jira_connector_type: str | None
    jira_key: str | None
    jira_status: str | None
    jira_status_category: str | None


class DepEdge(BaseModel):
    id: uuid.UUID
    step_id: uuid.UUID
    depends_on_step_id: uuid.UUID


class PlanGraphResponse(BaseModel):
    project: ProjectResponse
    steps: list[StepNode]
    deps: list[DepEdge]


# ── KI-Planer ────────────────────────────────────────────────────────────────

class PlanMessage(BaseModel):
    role: str   # user | assistant
    content: str


class PlanRequest(BaseModel):
    messages: list[PlanMessage]
    existing_graph: dict[str, Any] | None = None  # PlanGraphResponse as dict


class ProposedStep(BaseModel):
    temp_id: str
    title: str
    description: str = ""
    jira_issue_type: str = "task"
    duration_days: int = 1
    depends_on: list[str] = []
    parent_temp_id: str | None = None


class ToolActivity(BaseModel):
    tool: str          # web_search | web_fetch
    detail: str        # query or URL
    ok: bool = True


class PlanResponse(BaseModel):
    reply: str
    steps: list[ProposedStep]
    open_points: list[str] = []
    sources: list[str] = []
    tool_activity: list[ToolActivity] = []


class SavePlanRequest(BaseModel):
    name: str
    description: str | None = None
    steps: list[ProposedStep]
