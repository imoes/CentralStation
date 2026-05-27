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
    # KI-Agent: per-user CheckMK filters (lists of strings)
    checkmk_locations:   Mapped[list | None] = mapped_column(JSON)
    checkmk_ve:          Mapped[list | None] = mapped_column(JSON)
    checkmk_criticality: Mapped[list | None] = mapped_column(JSON)
    checkmk_os:          Mapped[list | None] = mapped_column(JSON)
    checkmk_hostgroups:  Mapped[list | None] = mapped_column(JSON)
    # Feed searches — list of UUIDs the user has explicitly disabled
    feed_disabled_search_ids: Mapped[list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class FeedSearch(Base):
    __tablename__ = "feed_searches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # NULL = system search (visible to all); set = personal search
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # OpenSearch index pattern, e.g. "cs-feed-graylog", "cs-feed-wazuh", "cs-feed-*"
    index_pattern: Mapped[str] = mapped_column(String(60), default="cs-feed-*")
    name: Mapped[str] = mapped_column(String(100))
    # Lucene/OpenSearch query string, empty = match_all
    query_string: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # When True: matching items are hidden from the main feed (must_not clause)
    is_exclusion: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DashboardWidget(Base):
    __tablename__ = "dashboard_widgets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    dashboard_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # "stat" | "list" | "donut" | "timeseries"
    widget_type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(100))
    # GridStack layout
    gs_x: Mapped[int] = mapped_column(Integer, default=0)
    gs_y: Mapped[int] = mapped_column(Integer, default=0)
    gs_w: Mapped[int] = mapped_column(Integer, default=4)
    gs_h: Mapped[int] = mapped_column(Integer, default=3)
    # Widget-specific config (data source, filters, promql, etc.)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
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
