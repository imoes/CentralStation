"""ITIL Work Session API — full ticket lifecycle with AI support."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.workflow import WorkSession

router = APIRouter(prefix="/workflow", tags=["workflow"])


# ── Pydantic schemas ────────────────────────────────────────────────────────────

class WorkSessionCreate(BaseModel):
    title: str
    jira_key: str | None = None
    jira_issue_id: str | None = None
    alert_id: str | None = None
    computer_session_id: str | None = None
    category: str | None = None
    subcategory: str | None = None
    impact: str | None = None
    urgency: str | None = None


class WorkSessionUpdate(BaseModel):
    title: str | None = None
    jira_key: str | None = None
    category: str | None = None
    subcategory: str | None = None
    impact: str | None = None
    urgency: str | None = None
    status: str | None = None
    closure_code: str | None = None
    resolution_type: str | None = None
    root_cause: str | None = None
    resolution_summary: str | None = None
    gitlab_project_id: str | None = None
    gitlab_branch: str | None = None
    gitlab_mr_iid: int | None = None
    gitlab_mr_url: str | None = None


class WorkNoteAdd(BaseModel):
    content: str
    note_type: str = "user"  # user | ai


class GenerateCommentRequest(BaseModel):
    comment_type: str = "progress"  # progress | pending | escalation | handoff
    additional_context: str | None = None  # recent developments / sprint context


class GenerateResolutionRequest(BaseModel):
    root_cause: str | None = None
    resolution_type: str = "permanent_fix"
    closure_code: str = "solved_permanently"


class PostCommentRequest(BaseModel):
    comment: str


class SuggestSolutionRequest(BaseModel):
    use_rag: bool = True
    use_web: bool = True


class AnalyzeMailRequest(BaseModel):
    subject: str
    preview: str


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _to_dict(s: WorkSession, jira_base_url: str | None = None) -> dict:
    browse_url = (
        f"{jira_base_url.rstrip('/')}/browse/{s.jira_key}"
        if jira_base_url and s.jira_key else None
    )
    return {
        "id": str(s.id),
        "user_id": str(s.user_id),
        "jira_key": s.jira_key,
        "jira_issue_id": s.jira_issue_id,
        "jira_browse_url": browse_url,
        "alert_id": str(s.alert_id) if s.alert_id else None,
        "computer_session_id": s.computer_session_id,
        "gitlab_project_id": s.gitlab_project_id,
        "gitlab_branch": s.gitlab_branch,
        "gitlab_mr_iid": s.gitlab_mr_iid,
        "gitlab_mr_url": s.gitlab_mr_url,
        "title": s.title,
        "category": s.category,
        "subcategory": s.subcategory,
        "impact": s.impact,
        "urgency": s.urgency,
        "priority": s.priority,
        "status": s.status,
        "closure_code": s.closure_code,
        "resolution_type": s.resolution_type,
        "work_notes": s.work_notes or [],
        "root_cause": s.root_cause,
        "resolution_summary": s.resolution_summary,
        "ai_suggested_solution": s.ai_suggested_solution,
        "kedb_references": s.kedb_references or [],
        "related_mail_ids": s.related_mail_ids or [],
        "sla_response_at": s.sla_response_at.isoformat() if s.sla_response_at else None,
        "sla_resolved_at": s.sla_resolved_at.isoformat() if s.sla_resolved_at else None,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


async def _get_jira_base_url(db: AsyncSession, jira_key: str | None = None) -> str | None:
    """Return the base URL of the Jira connector that owns this issue key.

    Checks both 'jira' and 'jira_sd' connector types. When multiple connectors
    exist and a jira_key is provided, tries each connector until one returns the
    issue, so ServiceDesk tickets (e.g. IMIT-*) get the correct browse URL
    instead of the regular Jira URL.
    """
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type.in_(["jira", "jira_sd"]),
            ConnectorConfig.enabled == True,
        )
    )
    connectors = result.scalars().all()
    if not connectors:
        return None
    if not jira_key or len(connectors) == 1:
        return connectors[0].base_url
    for conn in connectors:
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            await jira.get_issue_detail(jira_key)
            return conn.base_url
        except Exception:
            pass
    return connectors[0].base_url


async def _get_session(session_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> WorkSession:
    result = await db.execute(
        select(WorkSession).where(WorkSession.id == session_id, WorkSession.user_id == user_id)
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Session not found")
    return s


async def _get_llm(db: AsyncSession):
    from app.services.settings import get_llm_config
    llm = await get_llm_config(db)
    if not llm.is_configured:
        raise HTTPException(503, "LLM not configured")
    return llm


async def _build_ticket_context(s: WorkSession, db: AsyncSession) -> str:
    """Build full context string: Jira description + comments + local work notes."""
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    parts: list[str] = [f"Ticket: {s.title}"]

    if s.jira_key:
        try:
            conn_result = await db.execute(
                select(ConnectorConfig)
                .where(ConnectorConfig.type == "jira", ConnectorConfig.enabled.is_(True))
                .order_by(ConnectorConfig.owner_user_id.is_(None))
                .limit(1)
            )
            conn = conn_result.scalar_one_or_none()
            if conn:
                creds = decrypt_credentials(conn.encrypted_credentials)
                jira = JiraConnector(base_url=conn.base_url, credentials=creds)
                detail = await jira.get_issue_detail(s.jira_key)
                if detail.get("description"):
                    parts.append(f"\nBeschreibung:\n{detail['description']}")
                for c in (detail.get("comments") or []):
                    parts.append(f"\nKommentar von {c['author']} ({c['created'][:10]}):\n{c['body']}")
        except Exception:
            pass

    if s.root_cause:
        parts.append(f"\nRoot Cause: {s.root_cause}")

    for note in (s.work_notes or []):
        parts.append(f"\nArbeitsnotiz ({note.get('author', '')}): {note.get('content', '')}")

    return "\n".join(parts)


# ── CRUD ─────────────────────────────────────────────────────────────────────────

@router.get("")
async def list_sessions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
):
    q = (
        select(WorkSession)
        .where(WorkSession.user_id == user.id)
        .order_by(WorkSession.created_at.desc())
        .limit(limit)
    )
    if status:
        q = q.where(WorkSession.status == status)
    result = await db.execute(q)
    jira_url = await _get_jira_base_url(db)
    return [_to_dict(s, jira_url) for s in result.scalars().all()]


@router.post("", status_code=201)
async def create_session(
    body: WorkSessionCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.workflow_ai import calculate_priority

    session = WorkSession(
        user_id=user.id,
        title=body.title,
        jira_key=body.jira_key,
        jira_issue_id=body.jira_issue_id,
        alert_id=uuid.UUID(body.alert_id) if body.alert_id else None,
        computer_session_id=body.computer_session_id,
        category=body.category,
        subcategory=body.subcategory,
        impact=body.impact,
        urgency=body.urgency,
        work_notes=[],
    )
    if body.impact and body.urgency:
        p = calculate_priority(body.impact, body.urgency)
        session.priority = p["priority"]
        now = datetime.now(timezone.utc)
        session.sla_response_at = now + timedelta(minutes=p["response_minutes"])
        session.sla_resolved_at = now + timedelta(minutes=p["resolution_minutes"])

    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _to_dict(session, await _get_jira_base_url(db, session.jira_key))


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    s = await _get_session(session_id, user.id, db)
    jira_url = await _get_jira_base_url(db, s.jira_key)
    return _to_dict(s, jira_url)


@router.patch("/{session_id}")
async def update_session(
    session_id: uuid.UUID,
    body: WorkSessionUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.workflow_ai import calculate_priority

    s = await _get_session(session_id, user.id, db)
    updated = body.model_dump(exclude_none=True)
    for field, value in updated.items():
        setattr(s, field, value)
    if ("impact" in updated or "urgency" in updated) and s.impact and s.urgency:
        p = calculate_priority(s.impact, s.urgency)
        s.priority = p["priority"]
    await db.commit()
    return {"ok": True}


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    s = await _get_session(session_id, user.id, db)
    await db.delete(s)
    await db.commit()


# ── Work Notes ───────────────────────────────────────────────────────────────────

@router.post("/{session_id}/notes")
async def add_work_note(
    session_id: uuid.UUID,
    body: WorkNoteAdd,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    s = await _get_session(session_id, user.id, db)
    notes = list(s.work_notes or [])
    notes.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "author": user.full_name or user.email,
        "type": body.note_type,
        "content": body.content,
    })
    s.work_notes = notes
    await db.commit()
    return {"ok": True, "notes": notes}


# ── AI Actions ───────────────────────────────────────────────────────────────────

@router.post("/{session_id}/generate-comment")
async def generate_comment(
    session_id: uuid.UUID,
    body: GenerateCommentRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.ai_language import get_response_language_for_user
    from app.services.workflow_ai import generate_comment as ai_comment

    s = await _get_session(session_id, user.id, db)
    llm = await _get_llm(db)
    context = await _build_ticket_context(s, db)
    lang = await get_response_language_for_user(db, user.id)
    comment = await ai_comment(
        llm,
        s.title,
        context,
        s.work_notes or [],
        body.comment_type,
        additional_context=body.additional_context,
        db=db,
        lang=lang,
    )

    notes = list(s.work_notes or [])
    notes.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "author": "KI-Assistent",
        "type": "ai",
        "content": f"[KI-Kommentar / {body.comment_type}]\n{comment}",
    })
    s.work_notes = notes
    await db.commit()
    return {"comment": comment, "comment_type": body.comment_type}


@router.post("/{session_id}/post-comment")
async def post_comment_to_jira(
    session_id: uuid.UUID,
    body: PostCommentRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Post a comment text directly to the linked Jira ticket."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    s = await _get_session(session_id, user.id, db)
    if not s.jira_key:
        raise HTTPException(status_code=400, detail="Diese Work Session hat kein verknüpftes Jira-Ticket")

    from app.models.connector import ConnectorConfig as CC
    conn_result = await db.execute(
        select(CC).where(
            CC.type.in_(("jira", "jira_sd")),
            CC.enabled.is_(True),
            ((CC.owner_user_id == user.id) | CC.owner_user_id.is_(None)),
        ).order_by(CC.owner_user_id.is_(None), CC.updated_at.desc())
    )
    connectors = conn_result.scalars().all()
    if not connectors:
        raise HTTPException(status_code=503, detail="Jira nicht konfiguriert")

    last_err: Exception | None = None
    for conn in connectors:
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            result = await jira.add_comment(s.jira_key, body.comment)
            return {"success": True, "comment_id": result.get("id"), "jira_key": s.jira_key}
        except Exception as e:
            last_err = e

    raise HTTPException(status_code=502, detail=f"Kommentar konnte nicht gepostet werden: {last_err}")


@router.post("/{session_id}/generate-resolution")
async def generate_resolution(
    session_id: uuid.UUID,
    body: GenerateResolutionRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.ai_language import get_response_language_for_user
    from app.services.workflow_ai import generate_resolution as ai_resolution

    s = await _get_session(session_id, user.id, db)
    llm = await _get_llm(db)
    root_cause = body.root_cause or s.root_cause
    context = await _build_ticket_context(s, db)
    lang = await get_response_language_for_user(db, user.id)
    resolution = await ai_resolution(
        llm, s.title, context, s.work_notes or [],
        root_cause, body.resolution_type, body.closure_code, lang=lang,
    )
    s.resolution_summary = resolution
    s.closure_code = body.closure_code
    s.resolution_type = body.resolution_type
    if body.root_cause:
        s.root_cause = body.root_cause
    await db.commit()
    return {"resolution": resolution}


@router.post("/{session_id}/5why")
async def run_5why(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.ai_language import get_response_language_for_user
    from app.services.workflow_ai import run_5why_analysis

    s = await _get_session(session_id, user.id, db)
    llm = await _get_llm(db)
    context = await _build_ticket_context(s, db)
    lang = await get_response_language_for_user(db, user.id)
    analysis = await run_5why_analysis(llm, s.title, context, s.work_notes or [], lang=lang)
    if "root_cause" in analysis and not s.root_cause:
        s.root_cause = analysis["root_cause"]
        await db.commit()
    return analysis


@router.post("/{session_id}/suggest-solution")
async def suggest_solution(
    session_id: uuid.UUID,
    body: SuggestSolutionRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.ai_language import get_response_language_for_user
    from app.services.workflow_ai import suggest_solution as ai_suggest

    s = await _get_session(session_id, user.id, db)
    llm = await _get_llm(db)
    context = await _build_ticket_context(s, db)
    lang = await get_response_language_for_user(db, user.id)
    solution = await ai_suggest(llm, db, s.title, context, body.use_rag, body.use_web, lang=lang)
    if solution.get("solution_steps"):
        s.ai_suggested_solution = "\n".join(solution["solution_steps"])
        await db.commit()
    return solution


@router.post("/{session_id}/auto-categorize")
async def auto_categorize(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.ai_language import get_response_language_for_user
    from app.services.workflow_ai import auto_categorize as ai_cat, calculate_priority

    s = await _get_session(session_id, user.id, db)
    llm = await _get_llm(db)
    context = await _build_ticket_context(s, db)
    lang = await get_response_language_for_user(db, user.id)
    result = await ai_cat(llm, s.title, context, lang=lang)
    s.category = result.get("category", s.category)
    s.subcategory = result.get("subcategory", s.subcategory)
    if result.get("impact"):
        s.impact = result["impact"]
    if result.get("urgency"):
        s.urgency = result["urgency"]
    if s.impact and s.urgency:
        p = calculate_priority(s.impact, s.urgency)
        s.priority = p["priority"]
    await db.commit()
    return result


# ── Mail Analysis (stateless) ─────────────────────────────────────────────────────

# ── GitLab integration ──────────────────────────────────────────────

class GitLabBranchRequest(BaseModel):
    project_id: str
    branch: str
    ref: str = "main"


class GitLabMRRequest(BaseModel):
    target_branch: str
    title: str


@router.post("/{session_id}/gitlab/branch", status_code=201)
async def create_gitlab_branch(
    session_id: uuid.UUID,
    body: GitLabBranchRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.gitlab import GitLabConnector

    s = await _get_session(session_id, user.id, db)

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "gitlab",
            ConnectorConfig.enabled.is_(True),
        ).where(
            (ConnectorConfig.owner_user_id == user.id) |
            (ConnectorConfig.owner_user_id.is_(None))
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        from fastapi import HTTPException
        raise HTTPException(503, "No GitLab connector configured")

    creds = decrypt_credentials(cfg.encrypted_credentials)
    gl = GitLabConnector(base_url=cfg.base_url, credentials=creds)
    branch_data = await gl.create_branch(body.project_id, body.branch, body.ref)

    s.gitlab_project_id = body.project_id
    s.gitlab_branch = branch_data.get("name", body.branch)
    await db.commit()
    return {"branch": s.gitlab_branch, "project_id": s.gitlab_project_id}


@router.post("/{session_id}/gitlab/mr", status_code=201)
async def create_gitlab_mr(
    session_id: uuid.UUID,
    body: GitLabMRRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.gitlab import GitLabConnector

    s = await _get_session(session_id, user.id, db)
    if not s.gitlab_project_id or not s.gitlab_branch:
        from fastapi import HTTPException
        raise HTTPException(400, "Session has no GitLab project/branch linked yet")

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "gitlab",
            ConnectorConfig.enabled.is_(True),
        ).where(
            (ConnectorConfig.owner_user_id == user.id) |
            (ConnectorConfig.owner_user_id.is_(None))
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        from fastapi import HTTPException
        raise HTTPException(503, "No GitLab connector configured")

    creds = decrypt_credentials(cfg.encrypted_credentials)
    gl = GitLabConnector(base_url=cfg.base_url, credentials=creds)
    mr = await gl.create_merge_request(
        s.gitlab_project_id, s.gitlab_branch, body.target_branch, body.title
    )

    s.gitlab_mr_iid = mr.get("iid")
    s.gitlab_mr_url = mr.get("web_url")
    await db.commit()
    return {"iid": s.gitlab_mr_iid, "url": s.gitlab_mr_url, "title": mr.get("title")}


@router.get("/{session_id}/gitlab/status")
async def get_gitlab_status(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.gitlab import GitLabConnector

    s = await _get_session(session_id, user.id, db)
    if not s.gitlab_project_id:
        return {"linked": False}

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "gitlab",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"linked": True, "error": "No GitLab connector"}

    creds = decrypt_credentials(cfg.encrypted_credentials)
    gl = GitLabConnector(base_url=cfg.base_url, credentials=creds)

    pipelines = []
    mr_state = None
    if s.gitlab_branch:
        try:
            pipelines = await gl.list_pipelines(s.gitlab_project_id, ref=s.gitlab_branch)
            pipelines = pipelines[:3]
        except Exception:
            pass
    if s.gitlab_mr_iid:
        try:
            mrs = await gl.list_merge_requests(s.gitlab_project_id, state="all")
            mr = next((m for m in mrs if m["iid"] == s.gitlab_mr_iid), None)
            if mr:
                mr_state = mr.get("state")
        except Exception:
            pass

    return {
        "linked": True,
        "project_id": s.gitlab_project_id,
        "branch": s.gitlab_branch,
        "mr_iid": s.gitlab_mr_iid,
        "mr_url": s.gitlab_mr_url,
        "mr_state": mr_state,
        "pipelines": [{"id": p["id"], "status": p["status"]} for p in pipelines],
    }


@router.post("/analyze-mail")
async def analyze_mail(
    body: AnalyzeMailRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.ai_language import get_response_language_for_user
    from app.services.workflow_ai import analyze_mail as ai_mail

    llm = await _get_llm(db)
    lang = await get_response_language_for_user(db, user.id)
    return await ai_mail(llm, body.subject, body.preview, lang=lang)
