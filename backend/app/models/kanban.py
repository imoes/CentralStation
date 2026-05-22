import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KanbanCard(Base):
    __tablename__ = "kanban_cards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # backlog | todo | in_progress | review | done
    status: Mapped[str] = mapped_column(String(30), default="backlog", nullable=False, index=True)
    # critical | high | medium | low
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    jira_key: Mapped[str | None] = mapped_column(String(50), index=True)
    jira_issue_id: Mapped[str | None] = mapped_column(String(50))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL")
    )
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
