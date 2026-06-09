import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
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
    # Ticket seen map — {jira_key: ISO-timestamp} for badge tracking (server-side)
    ticket_seen_map: Mapped[dict | None] = mapped_column(JSON)
    # UI theme: "classic" | "holo" | "lcars"
    ui_theme: Mapped[str | None] = mapped_column(String(20), default="classic")
    # Feature flag: Hermes Computer Console (admin activates per user, default off)
    computer_console_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
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
    # Generative-UI: pinned widgets are never moved by the layout engine
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Generative-UI: hidden widgets are collapsed/invisible in generative mode
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
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
    # "classic" | "generative"
    mode: Mapped[str] = mapped_column(String(20), default="generative")
    # Generative dashboard only: the LLM's explanation for the chosen layout
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Generative dashboard only: when the AI last composed this layout
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AiInsightCache(Base):
    """Cached AI verdict per recurring alert, keyed by its dedup external_id.

    Avoids re-calling the (slow) LLM for alerts that keep firing — the same
    cmk:host:service gets analysed once and reused until the verdict expires.
    """
    __tablename__ = "ai_insight_cache"

    cache_key: Mapped[str] = mapped_column(String(120), primary_key=True)  # external_id
    severity: Mapped[str | None] = mapped_column(String(12))
    sample_title: Mapped[str | None] = mapped_column(String(300))
    verdict: Mapped[str | None] = mapped_column(Text)        # short "why + first action"
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorklistSnapshot(Base):
    """Latest AI-prioritised worklist for the bridge. One row replaced each run."""
    __tablename__ = "worklist_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    items: Mapped[list] = mapped_column(JSON, default=list)   # ranked worklist entries
    alert_state: Mapped[str] = mapped_column(String(10), default="green")
    open_count: Mapped[int] = mapped_column(Integer, default=0)


class AlertScoreAdjustment(Base):
    """Adaptive scoring: learned delta for a specific alert pattern.

    Pattern is identified by source + hashed title prefix.
    score_delta is added to the base CPU score — positive = LLM more likely,
    negative = LLM less likely. Expires after score_delta_decay_days days.
    """
    __tablename__ = "alert_score_adjustments"

    pattern_hash: Mapped[str] = mapped_column(String(12), primary_key=True)
    pattern_desc: Mapped[str | None] = mapped_column(String(200))
    score_delta: Mapped[float] = mapped_column(Float, default=0.0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class AlertCollaboration(Base):
    """Tracks claim/ownership and work_status per logical problem (keyed by external_id).

    external_id is the stable dedup key from OpenSearch — same alert source always
    produces the same external_id so this row stays consistent across re-aggregations.
    """
    __tablename__ = "alert_collaboration"

    external_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claimed_by_name: Mapped[str | None] = mapped_column(String(200))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    work_status: Mapped[str] = mapped_column(String(20), default="new")  # new|investigating|resolved
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AlertComment(Base):
    """Comments and activity timeline entries for an alert (by external_id).

    kind: comment | claim | release | status | ai
    System events (claim/release/status/ai) are also stored here to build
    a complete activity timeline in one query.
    """
    __tablename__ = "alert_comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="comment")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class Incident(Base):
    """Groups related alerts into a single incident for timeline + diagnosis."""
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    primary_host: Mapped[str] = mapped_column(String(255), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|investigating|resolved
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class IncidentMember(Base):
    """Links an alert (by external_id) to an incident."""
    __tablename__ = "incident_members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(40), default="")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ComputerSession(Base):
    """Persists Computer Console (Hermes) session metadata per user.

    The actual conversation history is stored in Hermes's own state.db
    (${PWD}/.hermes/state.db). This table only holds the metadata needed
    to list and restore sessions after page reload.
    id = the Hermes session UUID (used as session_id in AIAgent).
    """
    __tablename__ = "computer_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # Hermes session UUID
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100), default="Session")
    msg_count: Mapped[int] = mapped_column(Integer, default=0)
    # Alert external_id for handoff sessions — drives the "✓ GELÖST" button.
    # Persisted so the button survives reloads and container restarts.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
