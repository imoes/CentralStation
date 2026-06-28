"""Project planning API — CRUD + KI planner + Jira integration."""
from __future__ import annotations

import json
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAnyStaff
from app.core.database import get_db
from app.schemas.projects import (
    AttachTicketRequest,
    CreateTicketRequest,
    DepCreate,
    DepResponse,
    PlanGraphResponse,
    PlanRequest,
    PlanResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    ProposedStep,
    SavePlanRequest,
    StepCreate,
    StepResponse,
    StepUpdate,
)
import app.services.project_service as svc

router = APIRouter(prefix="/projects", tags=["projects"])

DB = Annotated[AsyncSession, Depends(get_db)]


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: DB, user: CurrentUser, search: str = ""):
    return await svc.list_projects(db, search)


@router.post("", response_model=ProjectResponse, status_code=201, dependencies=[RequireAnyStaff])
async def create_project(body: ProjectCreate, db: DB, user: CurrentUser):
    return await svc.create_project(db, body.name, body.description, str(user.id), body.status)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: DB, user: CurrentUser):
    return await svc.get_project(db, project_id)


@router.patch("/{project_id}", response_model=ProjectResponse, dependencies=[RequireAnyStaff])
async def update_project(project_id: str, body: ProjectUpdate, db: DB, user: CurrentUser):
    return await svc.update_project(db, project_id, **body.model_dump(exclude_none=True))


@router.delete("/{project_id}", status_code=204, dependencies=[RequireAnyStaff])
async def delete_project(project_id: str, db: DB, user: CurrentUser):
    await svc.delete_project(db, project_id)


# ── Graph ─────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/graph", response_model=PlanGraphResponse)
async def get_project_graph(project_id: str, db: DB, user: CurrentUser):
    return await svc.get_project_graph(db, project_id)


# ── Steps ─────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/steps", response_model=StepResponse, status_code=201, dependencies=[RequireAnyStaff])
async def add_step(project_id: str, body: StepCreate, db: DB, user: CurrentUser):
    return await svc.add_step(
        db, project_id,
        title=body.title,
        description=body.description,
        jira_issue_type=body.jira_issue_type,
        priority=body.priority,
        duration_days=body.duration_days,
        story_points=body.story_points,
        sort_order=body.sort_order,
        parent_step_id=str(body.parent_step_id) if body.parent_step_id else None,
        depends_on=[str(d) for d in body.depends_on],
        assignee=body.assignee,
        labels=body.labels if body.labels else None,
        due_date=body.due_date,
        acceptance_criteria=body.acceptance_criteria,
        pos_x=body.pos_x,
        pos_y=body.pos_y,
    )


@router.patch("/steps/{step_id}", response_model=StepResponse, dependencies=[RequireAnyStaff])
async def update_step(step_id: str, project_id: str, body: StepUpdate, db: DB, user: CurrentUser):
    return await svc.update_step(db, step_id, project_id, **body.model_dump(exclude_none=True))


@router.delete("/steps/{step_id}", status_code=204, dependencies=[RequireAnyStaff])
async def delete_step(step_id: str, project_id: str, db: DB, user: CurrentUser):
    await svc.delete_step(db, step_id, project_id)


# ── Dependencies ──────────────────────────────────────────────────────────────

@router.post("/{project_id}/deps", response_model=DepResponse, status_code=201, dependencies=[RequireAnyStaff])
async def add_dep(project_id: str, body: DepCreate, db: DB, user: CurrentUser):
    return await svc.add_dependency(
        db, project_id, str(body.step_id), str(body.depends_on_step_id)
    )


@router.delete("/deps/{dep_id}", status_code=204, dependencies=[RequireAnyStaff])
async def delete_dep(dep_id: str, project_id: str, db: DB, user: CurrentUser):
    await svc.remove_dependency(db, dep_id, project_id)


# ── Jira ──────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/steps/{step_id}/attach-ticket", response_model=StepResponse, dependencies=[RequireAnyStaff])
async def attach_ticket(project_id: str, step_id: str, body: AttachTicketRequest, db: DB, user: CurrentUser):
    return await svc.attach_jira_ticket(
        db, project_id, step_id, body.connector_type, body.jira_key, str(user.id)
    )


@router.post("/{project_id}/steps/{step_id}/create-ticket", response_model=StepResponse, dependencies=[RequireAnyStaff])
async def create_ticket(project_id: str, step_id: str, body: CreateTicketRequest, db: DB, user: CurrentUser):
    return await svc.create_jira_ticket_for_step(
        db, project_id, step_id,
        body.connector_type,
        body.summary,
        body.description,
        body.issue_type,
        body.epic_key,
        str(user.id),
    )


@router.post("/{project_id}/sync", response_model=dict, dependencies=[RequireAnyStaff])
async def sync_project(project_id: str, db: DB, user: CurrentUser):
    count = await svc.sync_jira_statuses(db, project_id)
    return {"updated": count}


# ── KI-Planer ─────────────────────────────────────────────────────────────────

@router.post("/plan", response_model=PlanResponse, dependencies=[RequireAnyStaff])
async def run_planner(body: PlanRequest, db: DB, user: CurrentUser):
    from app.services.settings import get_active_llm_config
    from app.services.llm_client import generate_text
    from app.services.ai_agent.prompts import PROJECT_PLANNER_SYSTEM

    llm = await get_active_llm_config(db, user.id)
    messages = [{"role": "system", "content": PROJECT_PLANNER_SYSTEM}]

    for m in body.messages:
        content = m.content
        if m.role == "user" and body.existing_graph and body.messages[-1] == m:
            content = f"{content}\n\n<existing_graph>\n{json.dumps(body.existing_graph, default=str)}\n</existing_graph>"
        messages.append({"role": m.role, "content": content})

    raw = await generate_text(llm, messages, max_output_tokens=4096)

    text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m_obj = re.search(r"\{.*\}", text, re.DOTALL)
        if m_obj:
            data = json.loads(m_obj.group())
        else:
            return PlanResponse(reply=raw, steps=[])

    reply = data.get("reply", "")
    steps_raw = data.get("steps", [])
    steps = [
        ProposedStep(
            temp_id=s.get("temp_id", f"t{i}"),
            title=s.get("title", "Schritt"),
            description=s.get("description", ""),
            jira_issue_type=s.get("jira_issue_type", "task"),
            duration_days=int(s.get("duration_days", 1)),
            depends_on=s.get("depends_on", []),
            parent_temp_id=s.get("parent_temp_id"),
        )
        for i, s in enumerate(steps_raw)
    ]
    return PlanResponse(reply=reply, steps=steps)


@router.post("/from-plan", response_model=ProjectResponse, status_code=201, dependencies=[RequireAnyStaff])
async def save_plan(body: SavePlanRequest, db: DB, user: CurrentUser):
    steps_dicts = [s.model_dump() for s in body.steps]
    project = await svc.create_project_from_plan(
        db, body.name, body.description, steps_dicts, str(user.id)
    )
    return project
