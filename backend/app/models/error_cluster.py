"""Fehler-Cluster: Muster, ihre Zählungen je 5 min und die erkannten Episoden.

Siehe services/error_clusters. Zählwerte sind ROH (jede Zeile), nicht dedupliziert.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LogTemplate(Base):
    """Ein Fehlermuster. Der Schlüssel ist md5(Muster)[:16] beim ersten Auftreten."""
    __tablename__ = "log_templates"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    template: Mapped[str] = mapped_column(Text)
    examples: Mapped[list | None] = mapped_column(postgresql.JSONB, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    muted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    muted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    muted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LogTemplateBucket(Base):
    """Zeilen eines Musters in einem 5-min-Fenster, mit den obersten 50 Containern."""
    __tablename__ = "log_template_buckets"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True,
                                                   index=True)
    count: Mapped[int] = mapped_column(Integer)
    #: Anzahl verschiedener Container — für die übliche Breite eines Musters (Ausbreitung).
    n_entities: Mapped[int] = mapped_column(Integer, default=0)
    entities: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
    #: False, wenn das Fenster bei Graylog gekappt war — dann ist count eine Untergrenze.
    complete: Mapped[bool] = mapped_column(Boolean, default=True)


class LogTemplateHourly(Base):
    """Stundensumme eines Musters, 28 Tage — Grundlast je Wochenstunde (wöchentliche Jobs)."""
    __tablename__ = "log_template_hourly"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True,
                                                 index=True)
    count: Mapped[int] = mapped_column(Integer)


class EntityBucket(Base):
    """Fehler je Container/Quelle und 5-min-Fenster — für "gleichzeitig auffällig"."""
    __tablename__ = "entity_buckets"

    entity: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True,
                                                   index=True)
    count: Mapped[int] = mapped_column(Integer)


class ErrorCluster(Base):
    """Eine Episode: ein Muster von Beginn des Ausbruchs bis 60 min Ruhe."""
    __tablename__ = "error_clusters"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(16), index=True)
    #: "log" | "checkmk_fleet"
    source: Mapped[str] = mapped_column(String(20), default="log")
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open|closed
    rules: Mapped[list | None] = mapped_column(postgresql.JSONB, nullable=True)
    reasons: Mapped[list | None] = mapped_column(postgresql.JSONB, nullable=True)
    notify_class: Mapped[str] = mapped_column(String(10), default="digest")  # immediate|digest
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    peak15: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    entities: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
    sources: Mapped[list | None] = mapped_column(postgresql.JSONB, nullable=True)
    notified_rate: Mapped[int] = mapped_column(Integer, default=0)
    notified_entities: Mapped[int] = mapped_column(Integer, default=0)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Wann die Mail tatsächlich raus ist. notified_at ohne mailed_at = beschlossen,
    #: aber (z. B. durch einen Neustart) nicht zugestellt — der Runner holt das nach.
    mailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triage: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
    triage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ErrorClusterState(Base):
    """Eine Zeile: Erkennungsstart und Drain3-Zustand.

    In Postgres statt Redis: Redis darf geleert werden, und ein verlorener Zustand
    erzwänge eine neue Lernphase ohne jede Meldung.
    """
    __tablename__ = "error_cluster_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    miner_state: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    last_bucket: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
