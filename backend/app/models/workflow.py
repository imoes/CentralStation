import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    setup_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    jira_project: Mapped[str | None] = mapped_column(String(50))
    jira_default_assignee_filter: Mapped[str] = mapped_column(String(20), default="me")
    sla_notify_p1_minutes: Mapped[int] = mapped_column(Integer, default=15)
    sla_notify_p2_minutes: Mapped[int] = mapped_column(Integer, default=60)
    notification_settings: Mapped[dict | None] = mapped_column(JSON)
    o365_mailbox: Mapped[str | None] = mapped_column(String(200))
    o365_folder: Mapped[str | None] = mapped_column(String(100), default="Inbox")
    # Feed preferences
    feed_checkmk_min_age_minutes: Mapped[int | None] = mapped_column(Integer, default=5)
    feed_sources_enabled: Mapped[list | None] = mapped_column(JSON)
    feed_teams_channels: Mapped[list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserJiraQuery(Base):
    __tablename__ = "user_jira_queries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    jql: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_widget: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class WorkSession(Base):
    __tablename__ = "work_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    jira_key: Mapped[str | None] = mapped_column(String(50), index=True)
    jira_issue_id: Mapped[str | None] = mapped_column(String(50))
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(100))
    subcategory: Mapped[str | None] = mapped_column(String(100))
    impact: Mapped[str | None] = mapped_column(String(20))
    urgency: Mapped[str | None] = mapped_column(String(20))
    priority: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="in_progress")
    closure_code: Mapped[str | None] = mapped_column(String(50))
    resolution_type: Mapped[str | None] = mapped_column(String(30))
    work_notes: Mapped[list | None] = mapped_column(JSON)
    root_cause: Mapped[str | None] = mapped_column(Text)
    resolution_summary: Mapped[str | None] = mapped_column(Text)
    ai_suggested_solution: Mapped[str | None] = mapped_column(Text)
    kedb_references: Mapped[list | None] = mapped_column(JSON)
    related_mail_ids: Mapped[list | None] = mapped_column(JSON)
    sla_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
