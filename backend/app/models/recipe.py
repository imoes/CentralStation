"""Rezept: was in einer gelösten Konsolen-Sitzung zur Diagnose geholfen hat.

Gebunden an ein Fehlermuster (LogTemplate.key — auch Flotten-Services haben einen) oder
einen Host. Die Schritte sind cs_policy-Vorlagen und laufen nur über den Knopf
„Prüfschritte ausführen" im Incident; alles andere steht nur als Text daneben.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    scope_type: Mapped[str] = mapped_column(String(10))
    scope_key: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[list] = mapped_column(postgresql.JSONB, default=list)
    other_commands: Mapped[list] = mapped_column(postgresql.JSONB, default=list)
    source_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_incident_id: Mapped[uuid.UUID | None] = mapped_column(postgresql.UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(postgresql.UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True,
                                                 default=lambda: datetime.now(timezone.utc))
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
