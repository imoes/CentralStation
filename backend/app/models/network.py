import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NetworkSwitchEvent(Base):
    __tablename__ = "network_switch_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    switch_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # nsa | nss | nsc
    switch_type: Mapped[str | None] = mapped_column(String(10))
    location_id: Mapped[int | None] = mapped_column(Integer)
    location_name: Mapped[str | None] = mapped_column(String(50))
    location_city: Mapped[str | None] = mapped_column(String(100))
    vendor: Mapped[str | None] = mapped_column(String(50))
    message: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)
    graylog_message_id: Mapped[str | None] = mapped_column(String(255))
    dedup_key: Mapped[str | None] = mapped_column(String(64), index=True)
    # new | acknowledged | resolved
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
