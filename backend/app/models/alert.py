import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # checkmk | graylog | wazuh
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # critical | high | medium | low | info
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    # new | acknowledged | resolved
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    # Standort aus ID-Generator / NetBox
    location_name: Mapped[str | None] = mapped_column(String(100))
    location_city: Mapped[str | None] = mapped_column(String(100))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
