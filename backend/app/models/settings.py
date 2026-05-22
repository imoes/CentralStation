import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GlobalSetting(Base):
    __tablename__ = "global_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_plain: Mapped[str | None] = mapped_column(String(1024))
    value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
