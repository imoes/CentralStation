import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PlaybookDraft(Base):
    __tablename__ = "playbook_drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512))
    yaml: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="drafted", index=True)
    # drafted → approved → published / rejected
    awx_template_id: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class RemediationProposal(Base):
    __tablename__ = "remediation_proposals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Alert context
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    host: Mapped[str | None] = mapped_column(String(255))
    finding_title: Mapped[str] = mapped_column(String(512))
    rationale: Mapped[str | None] = mapped_column(Text)

    # AWX target
    awx_template_id: Mapped[int | None] = mapped_column(Integer)
    awx_template_name: Mapped[str | None] = mapped_column(String(255))
    extra_vars: Mapped[dict | None] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high

    # Lifecycle
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    # proposed → approved/rejected → running → succeeded/failed/cancelled

    # AWX job result
    awx_job_id: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str | None] = mapped_column(Text)

    # Approval metadata
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Link to analysis that triggered this proposal
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_analyses.id", ondelete="SET NULL"), nullable=True
    )
