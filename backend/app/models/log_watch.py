"""Log-Überwachung: Katalog aller Felder der Quellen (zuerst Graylog).

Analysiert werden Felder und ihre Werte — dafür muss jedes Feld bekannt sein. Die
Rolle eines Feldes bestimmt, wie es ausgewertet wird (services/log_watch/catalog.py).
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FieldCatalog(Base):
    """Ein Feld einer Quelle mit gemessener Kardinalität, Abdeckung und Rolle."""
    __tablename__ = "field_catalog"

    quelle: Mapped[str] = mapped_column(String(32), primary_key=True)
    feld: Mapped[str] = mapped_column(String(255), primary_key=True)
    typ: Mapped[str] = mapped_column(String(64))
    numerisch: Mapped[bool] = mapped_column(Boolean, default=False)
    #: entitaet | kategorie | kennung | messwert | freitext | ignorieren | unbekannt
    rolle: Mapped[str] = mapped_column(String(16))
    #: regel | admin — eine Messung überschreibt eine Admin-Rolle nie
    rolle_herkunft: Mapped[str] = mapped_column(String(16), default="regel")
    rolle_grund: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: aktiv (Werte im Messfenster) | ohne_werte (bekannt, im Fenster leer) |
    #: nicht_gemessen (Graylog lehnte die Messung ab) | verschwunden (Graylog kennt es nicht mehr)
    status: Mapped[str] = mapped_column(String(16), default="aktiv")
    kardinalitaet: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Nachrichten mit diesem Feld im Messfenster
    mit_feld: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Anteil der Nachrichten im Messfenster, die das Feld tragen (0..1)
    abdeckung: Mapped[float | None] = mapped_column(Float, nullable=True)
    beispielwerte: Mapped[list | None] = mapped_column(postgresql.JSONB, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: zuletzt mit Werten gemessen
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: letzte erfolgreiche Messung; None = noch nie gemessen
    gemessen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RasterLauf(Base):
    """Trockenlauf des Rasters: je 5-min-Fenster und Quelle, was gespeichert WÜRDE.

    Nur Zahlen (raster.zaehle), keine Nachrichten — daraus wird der Platzbedarf
    hochgerechnet, bevor `cs-logs-*` befüllt wird.
    """
    __tablename__ = "raster_lauf"

    fenster_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    #: graylog | wazuh | checkmk | uebergreifend
    quelle: Mapped[str] = mapped_column(String(16), primary_key=True)
    werte: Mapped[dict] = mapped_column(postgresql.JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
