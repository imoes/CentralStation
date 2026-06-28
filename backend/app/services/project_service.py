"""Shared project service — used by both REST API and MCP tools.

All write operations broadcast a 'project_updated' WS event so the frontend
(and the Werkbank-side) sees changes in real time.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

from app.models.projects import Project, ProjectStep, ProjectStepDep
from app.schemas.projects import (
    PlanGraphResponse,
    ProjectResponse,
    StepNode,
    DepEdge,
)
from app.services.project_cpm import compute_cpm


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _broadcast(project_id: str) -> None:
    try:
        from app.api.ws import manager
        await manager.broadcast({"type": "project_updated", "project_id": project_id})
    except Exception:
        pass


async def _get_project_or_404(db: AsyncSession, project_id: str) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == uuid.UUID(project_id))
    )
    project = result.scalar_one_or_none()
    if not project:
        from fastapi import HTTPException
        raise HTTPException(404, f"Project {project_id} not found")
    return project


# ── Project CRUD ─────────────────────────────────────────────────────────────

async def list_projects(db: AsyncSession, search: str = "") -> list[Project]:
    q = select(Project).order_by(Project.updated_at.desc())
    if search:
        q = q.where(Project.name.ilike(f"%{search}%"))
    result = await db.execute(q)
    return list(result.scalars())


async def create_project(db: AsyncSession, name: str, description: str | None, owner_id: str | None, status: str = "planning") -> Project:
    project = Project(
        name=name,
        description=description,
        owner_id=uuid.UUID(owner_id) if owner_id else None,
        status=status,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    await _broadcast(str(project.id))
    return project


async def get_project(db: AsyncSession, project_id: str) -> Project:
    return await _get_project_or_404(db, project_id)


async def update_project(db: AsyncSession, project_id: str, **kwargs) -> Project:
    project = await _get_project_or_404(db, project_id)
    for k, v in kwargs.items():
        if v is not None and hasattr(project, k):
            setattr(project, k, v)
    project.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project)
    await _broadcast(project_id)
    return project


async def delete_project(db: AsyncSession, project_id: str) -> None:
    project = await _get_project_or_404(db, project_id)
    await db.delete(project)
    await db.commit()
    await _broadcast(project_id)


# ── Graph read ───────────────────────────────────────────────────────────────

async def get_project_graph(db: AsyncSession, project_id: str) -> PlanGraphResponse:
    project = await _get_project_or_404(db, project_id)

    steps_result = await db.execute(
        select(ProjectStep)
        .where(ProjectStep.project_id == project.id)
        .order_by(ProjectStep.sort_order, ProjectStep.created_at)
    )
    steps = list(steps_result.scalars())

    deps_result = await db.execute(
        select(ProjectStepDep).where(
            ProjectStepDep.step_id.in_([s.id for s in steps])
        )
    )
    deps = list(deps_result.scalars())

    # Run CPM
    step_dicts = [{"id": s.id, "duration_days": s.duration_days} for s in steps]
    dep_dicts = [{"step_id": d.step_id, "depends_on_step_id": d.depends_on_step_id} for d in deps]
    cpm = compute_cpm(step_dicts, dep_dicts)

    step_nodes: list[StepNode] = []
    for s in steps:
        cpm_data = cpm.get(str(s.id), {})
        step_nodes.append(StepNode(
            id=s.id,
            parent_step_id=s.parent_step_id,
            title=s.title,
            description=s.description,
            status=s.status,
            jira_issue_type=s.jira_issue_type,
            priority=s.priority,
            duration_days=s.duration_days,
            story_points=s.story_points,
            sort_order=s.sort_order,
            assignee=s.assignee,
            labels=s.labels,
            due_date=s.due_date,
            acceptance_criteria=s.acceptance_criteria,
            est_start=cpm_data.get("es"),
            est_end=cpm_data.get("ef"),
            lst_start=cpm_data.get("ls"),
            lst_end=cpm_data.get("lf"),
            slack=cpm_data.get("slack"),
            critical=bool(cpm_data.get("critical", False)),
            pos_x=s.pos_x,
            pos_y=s.pos_y,
            jira_connector_type=s.jira_connector_type,
            jira_key=s.jira_key,
            jira_status=s.jira_status,
            jira_status_category=s.jira_status_category,
        ))

    dep_edges = [DepEdge(id=d.id, step_id=d.step_id, depends_on_step_id=d.depends_on_step_id) for d in deps]

    return PlanGraphResponse(
        project=ProjectResponse.model_validate(project),
        steps=step_nodes,
        deps=dep_edges,
    )


# ── Step CRUD ─────────────────────────────────────────────────────────────────

async def add_step(
    db: AsyncSession,
    project_id: str,
    title: str,
    description: str | None = None,
    jira_issue_type: str = "task",
    priority: str = "medium",
    duration_days: int = 1,
    story_points: int | None = None,
    sort_order: int = 0,
    parent_step_id: str | None = None,
    depends_on: list[str] | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    due_date: date | None = None,
    acceptance_criteria: str | None = None,
    pos_x: int | None = None,
    pos_y: int | None = None,
) -> ProjectStep:
    await _get_project_or_404(db, project_id)

    step = ProjectStep(
        project_id=uuid.UUID(project_id),
        parent_step_id=uuid.UUID(parent_step_id) if parent_step_id else None,
        title=title,
        description=description,
        jira_issue_type=jira_issue_type,
        priority=priority,
        duration_days=duration_days,
        story_points=story_points,
        sort_order=sort_order,
        assignee=assignee,
        labels=json.dumps(labels) if labels is not None else None,
        due_date=due_date,
        acceptance_criteria=acceptance_criteria,
        pos_x=pos_x,
        pos_y=pos_y,
    )
    db.add(step)
    await db.flush()

    for dep_id in (depends_on or []):
        db.add(ProjectStepDep(step_id=step.id, depends_on_step_id=uuid.UUID(dep_id)))

    await db.commit()
    await db.refresh(step)
    await _broadcast(project_id)
    return step


async def update_step(db: AsyncSession, step_id: str, project_id: str, **kwargs) -> ProjectStep:
    result = await db.execute(
        select(ProjectStep).where(
            ProjectStep.id == uuid.UUID(step_id),
            ProjectStep.project_id == uuid.UUID(project_id),
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        from fastapi import HTTPException
        raise HTTPException(404, f"Step {step_id} not found")

    # Track which Jira-relevant fields changed before saving
    jira_changes: dict[str, Any] = {}
    if step.jira_key:
        for field in ("title", "description", "priority", "status"):
            if field in kwargs and kwargs[field] != getattr(step, field, None):
                jira_changes[field] = kwargs[field]

    for k, v in kwargs.items():
        if v is None or not hasattr(step, k):
            continue
        if k == "labels" and isinstance(v, list):
            setattr(step, k, json.dumps(v))
        else:
            setattr(step, k, v)
    step.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(step)
    await _broadcast(project_id)

    # Push changed fields to Jira (non-blocking — failure never aborts the save)
    if step.jira_key and jira_changes:
        try:
            await _push_step_to_jira(db, step, jira_changes)
        except Exception as exc:
            log.warning("Jira push failed for step %s: %s", step_id, exc)

    return step


async def _push_step_to_jira(db: AsyncSession, step: ProjectStep, changes: dict[str, Any]) -> None:
    """Push local step changes to the linked Jira ticket."""
    from app.api.kanban import _get_preferred_connector
    from app.services.connectors.jira import JiraConnector

    connector_type = step.jira_connector_type or "jira"
    connector = await _get_preferred_connector(db, connector_type, None)
    if not connector:
        return
    jira = JiraConnector(connector)

    # Map priority to Jira priority names
    PRIORITY_MAP = {
        "highest": "Highest", "high": "High", "medium": "Medium",
        "low": "Low", "lowest": "Lowest",
    }

    # Collect field updates
    update_kwargs: dict[str, Any] = {}
    if "title" in changes:
        update_kwargs["summary"] = changes["title"]
    if "description" in changes:
        update_kwargs["description"] = changes["description"] or ""
    if "priority" in changes:
        update_kwargs["priority"] = PRIORITY_MAP.get(changes["priority"], "Medium")

    if update_kwargs:
        await jira.update_issue(step.jira_key, **update_kwargs)

    # Status → transition
    if "status" in changes:
        STATUS_CANDIDATES = {
            "done":        ["Done", "Erledigt", "Closed", "Resolved", "Fertig"],
            "in_progress": ["In Progress", "In Bearbeitung", "In Arbeit", "Open", "Offen"],
            "pending":     ["To Do", "Offen", "Open", "New", "Backlog"],
        }
        candidates = STATUS_CANDIDATES.get(changes["status"], [])
        if candidates:
            await jira.transition_issue_by_candidates(step.jira_key, candidates)


async def pull_step_from_jira(db: AsyncSession, step_id: str, project_id: str) -> ProjectStep:
    """Fetch latest Jira status and sync it back to the step."""
    step = await _get_step(db, step_id, project_id)
    if not step.jira_key:
        from fastapi import HTTPException
        raise HTTPException(400, "Step has no linked Jira ticket")

    from app.api.kanban import _get_preferred_connector
    from app.services.connectors.jira import JiraConnector

    connector_type = step.jira_connector_type or "jira"
    connector = await _get_preferred_connector(db, connector_type, None)
    if not connector:
        from fastapi import HTTPException
        raise HTTPException(404, "Jira connector not found")

    jira = JiraConnector(connector)
    issue = await jira.get_issue_detail(step.jira_key)
    fields = issue.get("fields", {})

    step.jira_status = fields.get("status", {}).get("name")
    cat = fields.get("status", {}).get("statusCategory", {}).get("key", "")
    step.jira_status_category = _map_jira_category(cat)
    step.jira_synced_at = datetime.now(timezone.utc)
    _derive_step_status(step)

    # Sync back assignee if available
    assignee = fields.get("assignee") or {}
    if assignee.get("displayName"):
        step.assignee = assignee["displayName"]

    step.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(step)
    await _broadcast(project_id)
    return step


async def delete_step(db: AsyncSession, step_id: str, project_id: str) -> None:
    result = await db.execute(
        select(ProjectStep).where(
            ProjectStep.id == uuid.UUID(step_id),
            ProjectStep.project_id == uuid.UUID(project_id),
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        from fastapi import HTTPException
        raise HTTPException(404, f"Step {step_id} not found")
    await db.delete(step)
    await db.commit()
    await _broadcast(project_id)


# ── Dependency CRUD ──────────────────────────────────────────────────────────

def _has_cycle(adjacency: dict[str, list[str]], start: str) -> bool:
    """DFS cycle check from `start`."""
    visited: set[str] = set()
    stack: list[str] = [start]
    while stack:
        node = stack.pop()
        if node == start and len(visited) > 0:
            return True
        if node in visited:
            continue
        visited.add(node)
        stack.extend(adjacency.get(node, []))
    return False


async def add_dependency(
    db: AsyncSession,
    project_id: str,
    step_id: str,
    depends_on_step_id: str,
) -> ProjectStepDep:
    # Cycle check: after adding (depends_on → step), would step eventually reach depends_on?
    # i.e., does depends_on already depend (transitively) on step?
    deps_result = await db.execute(
        select(ProjectStepDep).where(
            ProjectStepDep.step_id.in_(
                select(ProjectStep.id).where(ProjectStep.project_id == uuid.UUID(project_id))
            )
        )
    )
    existing = list(deps_result.scalars())
    adjacency: dict[str, list[str]] = {}
    for d in existing:
        adjacency.setdefault(str(d.depends_on_step_id), []).append(str(d.step_id))
    # Temporarily add the new edge
    adjacency.setdefault(depends_on_step_id, []).append(step_id)

    # Check if step_id can reach depends_on_step_id (would create cycle)
    visited: set[str] = set()
    queue = [step_id]
    while queue:
        cur = queue.pop()
        if cur == depends_on_step_id:
            from fastapi import HTTPException
            raise HTTPException(400, "Adding this dependency would create a cycle")
        if cur in visited:
            continue
        visited.add(cur)
        queue.extend(adjacency.get(cur, []))

    dep = ProjectStepDep(
        step_id=uuid.UUID(step_id),
        depends_on_step_id=uuid.UUID(depends_on_step_id),
    )
    db.add(dep)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        from fastapi import HTTPException
        raise HTTPException(409, "Dependency already exists")
    await db.refresh(dep)
    await _broadcast(project_id)
    return dep


async def remove_dependency(db: AsyncSession, dep_id: str, project_id: str) -> None:
    result = await db.execute(
        select(ProjectStepDep).where(ProjectStepDep.id == uuid.UUID(dep_id))
    )
    dep = result.scalar_one_or_none()
    if not dep:
        from fastapi import HTTPException
        raise HTTPException(404, f"Dependency {dep_id} not found")
    await db.delete(dep)
    await db.commit()
    await _broadcast(project_id)


# ── Jira integration ──────────────────────────────────────────────────────────

async def _get_step(db: AsyncSession, step_id: str, project_id: str) -> ProjectStep:
    result = await db.execute(
        select(ProjectStep).where(
            ProjectStep.id == uuid.UUID(step_id),
            ProjectStep.project_id == uuid.UUID(project_id),
        )
    )
    step = result.scalar_one_or_none()
    if not step:
        from fastapi import HTTPException
        raise HTTPException(404, f"Step {step_id} not found")
    return step


async def attach_jira_ticket(
    db: AsyncSession,
    project_id: str,
    step_id: str,
    connector_type: str,
    jira_key: str,
    user_id: str,
) -> ProjectStep:
    from app.api.kanban import _get_preferred_connector
    from app.services.connectors.jira import JiraConnector

    step = await _get_step(db, step_id, project_id)
    connector = await _get_preferred_connector(db, connector_type, uuid.UUID(user_id))
    if not connector:
        from fastapi import HTTPException
        raise HTTPException(404, f"No connector of type '{connector_type}' found")

    jira = JiraConnector(connector)
    issue = await jira.get_issue_detail(jira_key)

    step.jira_connector_type = connector_type
    step.jira_key = issue.get("key") or jira_key
    step.jira_issue_id = str(issue.get("id") or "")
    step.jira_status = issue.get("fields", {}).get("status", {}).get("name")
    cat = issue.get("fields", {}).get("status", {}).get("statusCategory", {}).get("key", "")
    step.jira_status_category = _map_jira_category(cat)
    step.jira_synced_at = datetime.now(timezone.utc)
    _derive_step_status(step)
    step.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(step)
    await _broadcast(project_id)
    return step


async def create_jira_ticket_for_step(
    db: AsyncSession,
    project_id: str,
    step_id: str,
    connector_type: str,
    summary: str | None,
    description: str | None,
    issue_type: str,
    epic_key: str | None,
    user_id: str,
) -> ProjectStep:
    from app.api.kanban import _get_preferred_connector
    from app.services.connectors.jira import JiraConnector

    step = await _get_step(db, step_id, project_id)
    connector = await _get_preferred_connector(db, connector_type, uuid.UUID(user_id))
    if not connector:
        from fastapi import HTTPException
        raise HTTPException(404, f"No connector of type '{connector_type}' found")

    jira = JiraConnector(connector)
    proj_key = connector.config.get("project_key", "IMIT")

    fields: dict[str, Any] = {
        "project": {"key": proj_key},
        "summary": summary or step.title,
        "description": description or step.description or "",
        "issuetype": {"name": issue_type},
    }
    if epic_key and issue_type.lower() not in ("epic",):
        fields["customfield_10014"] = epic_key  # Epic Link field

    issue = await jira.create_issue(fields)

    step.jira_connector_type = connector_type
    step.jira_key = issue.get("key")
    step.jira_issue_id = str(issue.get("id") or "")
    step.jira_status = "Open"
    step.jira_status_category = "new"
    step.jira_synced_at = datetime.now(timezone.utc)
    step.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(step)
    await _broadcast(project_id)
    return step


async def sync_jira_statuses(db: AsyncSession, project_id: str) -> int:
    """Poll all linked Jira tickets for the project, return number updated."""
    from app.api.kanban import _get_preferred_connector
    from app.services.connectors.jira import JiraConnector

    steps_result = await db.execute(
        select(ProjectStep).where(
            ProjectStep.project_id == uuid.UUID(project_id),
            ProjectStep.jira_key.isnot(None),
        )
    )
    steps = list(steps_result.scalars())
    if not steps:
        return 0

    # Group by connector_type for batch queries
    by_connector: dict[str, list[ProjectStep]] = {}
    for s in steps:
        ct = s.jira_connector_type or "jira"
        by_connector.setdefault(ct, []).append(s)

    updated = 0
    for connector_type, csteps in by_connector.items():
        keys = [s.jira_key for s in csteps if s.jira_key]
        if not keys:
            continue
        connector = await _get_preferred_connector(db, connector_type, None)
        if not connector:
            continue
        jira = JiraConnector(connector)
        jql = f'issueKey in ({",".join(keys)})'
        issues = await jira.search_issues(jql, fields=["status"])
        key_to_issue: dict[str, dict] = {i["key"]: i for i in issues}

        for s in csteps:
            if s.jira_key not in key_to_issue:
                continue
            issue = key_to_issue[s.jira_key]
            s.jira_status = issue.get("fields", {}).get("status", {}).get("name")
            cat = issue.get("fields", {}).get("status", {}).get("statusCategory", {}).get("key", "")
            s.jira_status_category = _map_jira_category(cat)
            s.jira_synced_at = datetime.now(timezone.utc)
            _derive_step_status(s)
            s.updated_at = datetime.now(timezone.utc)
            updated += 1

    await db.commit()
    await _broadcast(project_id)
    return updated


def _map_jira_category(cat_key: str) -> str:
    if cat_key in ("done", "complete"):
        return "done"
    if cat_key in ("indeterminate", "in_progress"):
        return "indeterminate"
    return "new"


def _derive_step_status(step: ProjectStep) -> None:
    cat = step.jira_status_category or "new"
    if cat == "done":
        step.status = "done"
    elif cat == "indeterminate":
        step.status = "in_progress"
    else:
        step.status = "pending"


# ── Ready steps (für Kanban) ──────────────────────────────────────────────────

async def get_ready_steps(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all steps that are actionable: not done, and all upstream deps done."""
    # Load all non-done steps + their projects
    from sqlalchemy.orm import selectinload
    steps_res = await db.execute(
        select(ProjectStep).where(ProjectStep.status != "done")
    )
    steps = list(steps_res.scalars())
    if not steps:
        return []

    step_ids = [s.id for s in steps]
    all_ids = {s.id for s in steps}

    # Load deps for these steps
    deps_res = await db.execute(
        select(ProjectStepDep).where(ProjectStepDep.step_id.in_(step_ids))
    )
    deps = list(deps_res.scalars())

    # For each step find its upstream deps
    step_to_deps: dict[Any, list[Any]] = {s.id: [] for s in steps}
    for d in deps:
        step_to_deps[d.step_id].append(d.depends_on_step_id)

    # Load statuses of upstream deps (they may be done and thus not in `steps`)
    upstream_ids = {d.depends_on_step_id for d in deps}
    done_ids: set[Any] = set()
    if upstream_ids:
        up_res = await db.execute(
            select(ProjectStep.id, ProjectStep.status).where(ProjectStep.id.in_(upstream_ids))
        )
        for row in up_res:
            if row.status == "done":
                done_ids.add(row.id)

    # Projects lookup
    project_ids = {s.project_id for s in steps}
    proj_res = await db.execute(select(Project).where(Project.id.in_(project_ids)))
    projects = {p.id: p for p in proj_res.scalars()}

    ready = []
    for s in steps:
        upstream = step_to_deps[s.id]
        # A step is "ready" if it has no upstream deps OR all upstream are done
        if all(dep_id in done_ids for dep_id in upstream):
            proj = projects.get(s.project_id)
            ready.append({
                "step_id": str(s.id),
                "project_id": str(s.project_id),
                "project_name": proj.name if proj else "?",
                "title": s.title,
                "jira_issue_type": s.jira_issue_type,
                "priority": s.priority,
                "status": s.status,
                "jira_key": s.jira_key,
                "assignee": s.assignee,
            })
    return ready


# ── Bulk create from KI plan ──────────────────────────────────────────────────

async def create_project_from_plan(
    db: AsyncSession,
    name: str,
    description: str | None,
    proposed_steps: list[dict[str, Any]],
    owner_id: str | None,
) -> Project:
    """Create a project + steps + deps from the KI planner's proposed graph."""
    project = Project(
        name=name,
        description=description,
        owner_id=uuid.UUID(owner_id) if owner_id else None,
        status="planning",
    )
    db.add(project)
    await db.flush()

    temp_to_id: dict[str, uuid.UUID] = {}
    step_objects: list[tuple[ProjectStep, list[str]]] = []

    for i, s in enumerate(proposed_steps):
        temp_id = s.get("temp_id", f"t{i}")
        parent_temp = s.get("parent_temp_id")
        code_blocks = s.get("code_blocks", [])
        bash_commands = s.get("bash_commands", [])
        impl_notes: str | None = None
        if code_blocks or bash_commands:
            impl_notes = json.dumps({"code_blocks": code_blocks, "bash_commands": bash_commands}, ensure_ascii=False)
        step = ProjectStep(
            project_id=project.id,
            title=s["title"],
            description=s.get("description") or None,
            jira_issue_type=s.get("jira_issue_type", "task"),
            duration_days=int(s.get("duration_days", 1)),
            sort_order=i,
            implementation_notes=impl_notes,
        )
        db.add(step)
        await db.flush()
        temp_to_id[temp_id] = step.id
        step_objects.append((step, s.get("depends_on", [])))

        if parent_temp and parent_temp in temp_to_id:
            step.parent_step_id = temp_to_id[parent_temp]

    # Second pass: wire up parent refs resolved after flush + deps
    for step, dep_temp_ids in step_objects:
        for dep_temp in dep_temp_ids:
            if dep_temp in temp_to_id:
                db.add(ProjectStepDep(
                    step_id=step.id,
                    depends_on_step_id=temp_to_id[dep_temp],
                ))

    await db.commit()
    await db.refresh(project)
    await _broadcast(str(project.id))
    return project
