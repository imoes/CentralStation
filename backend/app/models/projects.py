import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # planning | active | done | archived
    status: Mapped[str] = mapped_column(String(30), default="planning", nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProjectStep(Base):
    __tablename__ = "project_steps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Parent for hierarchical Epic → Task → Subtask structure
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_steps.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # pending | in_progress | done
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    # Jira issue type: epic | story | task | subtask | bug
    jira_issue_type: Mapped[str] = mapped_column(String(30), default="task", nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # CPM-computed fields (day offsets from project start)
    est_start: Mapped[int | None] = mapped_column(Integer)
    est_end: Mapped[int | None] = mapped_column(Integer)
    lst_start: Mapped[int | None] = mapped_column(Integer)
    lst_end: Mapped[int | None] = mapped_column(Integer)
    slack: Mapped[int | None] = mapped_column(Integer)
    # Jira-compatible card fields
    # highest | high | medium | low | lowest
    priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(256))        # name or email
    labels: Mapped[str | None] = mapped_column(Text)                 # JSON array ["label1","label2"]
    story_points: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)    # markdown
    # Cytoscape layout persistence
    pos_x: Mapped[int | None] = mapped_column(Integer)
    pos_y: Mapped[int | None] = mapped_column(Integer)
    # Jira issue linkage (one primary issue per step)
    jira_connector_type: Mapped[str | None] = mapped_column(String(30))   # jira | jira_sd
    jira_key: Mapped[str | None] = mapped_column(String(50), index=True)
    jira_issue_id: Mapped[str | None] = mapped_column(String(50))
    jira_status: Mapped[str | None] = mapped_column(String(100))
    # new | indeterminate | done
    jira_status_category: Mapped[str | None] = mapped_column(String(30))
    jira_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProjectStepDep(Base):
    __tablename__ = "project_step_deps"
    __table_args__ = (
        UniqueConstraint("step_id", "depends_on_step_id", name="uq_step_dep"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_steps.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_steps.id", ondelete="CASCADE"), nullable=False
    )
