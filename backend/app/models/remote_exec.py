"""Protokoll aller Lese-Vorlagen, die auf Servern liefen (Prüf-Container).

Wer hat wann was auf welchem Host gelesen — für MCP-Werkzeuge der Konsole, Prüfschritte
und Rezepte. Die Ausgabe selbst wird nicht gespeichert (kann sensibel sein), nur ihre Größe.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RemoteExecLog(Base):
    __tablename__ = "remote_exec_log"

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True,
                                         default=lambda: datetime.now(timezone.utc))
    user_id: Mapped[uuid.UUID | None] = mapped_column(postgresql.UUID(as_uuid=True), nullable=True)
    source: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(255), index=True)
    template: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(postgresql.JSONB, default=dict)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
