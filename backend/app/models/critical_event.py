"""Schwere Kernelmeldung (OOM-Kill, Kernel panic, I/O-Fehler …) eines Hosts in einem
5-min-Fenster — ein Ereignis je Host, Art und Fenster (services/error_clusters/critical.py).

Die Fehlererkennung zählt Menge; ein OOM ist aber 1–3 Zeilen auf einem Host und ging
darin unter (14.10.: docker0209 hatte 25+ Stunden lang jede Stunde einen OOM). Hier wird
jedes solche Ereignis einzeln festgehalten, damit eine Wiederholung erkannt werden kann.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CriticalEvent(Base):
    __tablename__ = "critical_events"
    __table_args__ = (UniqueConstraint("key", "window_start", name="uq_critical_events_key_window"),)

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(16), index=True)
    signature: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(255), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lines: Mapped[int] = mapped_column(Integer, default=1)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(timezone.utc))
