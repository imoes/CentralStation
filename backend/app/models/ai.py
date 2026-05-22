import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # sysadmin | network
    agent_type: Mapped[str] = mapped_column(String(30), default="sysadmin", nullable=False)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    sources_checked: Mapped[dict | None] = mapped_column(JSON)
    findings: Mapped[list | None] = mapped_column(JSON)
    recommendations: Mapped[list | None] = mapped_column(JSON)
    severity_summary: Mapped[str | None] = mapped_column(String(20))
    jira_tickets_created: Mapped[list | None] = mapped_column(JSON)
    rag_queries_used: Mapped[list | None] = mapped_column(JSON)
    token_usage: Mapped[dict | None] = mapped_column(JSON)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(8, 2))
