"""Log-Überwachung: Katalog aller Felder der Quellen (zuerst Graylog).

Analysiert werden Felder und ihre Werte — dafür muss jedes Feld bekannt sein. Die
Rolle eines Feldes bestimmt, wie es ausgewertet wird (services/log_watch/catalog.py).
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, LargeBinary, String, Text
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


class RelevanzDefinition(Base):
    """Was relevant ist und was nicht — versioniert (services/log_watch/definition.py).

    Jede Änderung ist eine neue Zeile mit Begründung; Urteile nennen Version und Kriterien.
    """
    __tablename__ = "relevanz_definition"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    kriterien: Mapped[list] = mapped_column(postgresql.JSONB)
    parameter: Mapped[dict] = mapped_column(postgresql.JSONB)
    #: Regelwerk (Firewall-Semantik); None = Standardregeln aus den Parametern
    regeln: Mapped[list | None] = mapped_column(postgresql.JSONB, nullable=True)
    #: Objekte (Name → Filtertext); None = Standardobjekte
    objekte: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
    begruendung: Mapped[str] = mapped_column(String(1000))
    erstellt_von: Mapped[str] = mapped_column(String(255))
    erstellt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetzZustand(Base):
    """Switch-Durchlauf: Lerner-Zustand (Drain, eigene Instanz) und Fortschritt — eine Zeile.

    In Postgres, damit ein Neustart nicht zu einer neuen Lernphase führt (wie error_clusters).
    """
    __tablename__ = "netz_zustand"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    gestartet_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    miner_state: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    letztes_fenster: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NetzMuster(Base):
    """Ein Switch-Meldungsmuster mit erstem und letztem Auftreten (für „neu“)."""
    __tablename__ = "netz_muster"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    template: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    beispiel: Mapped[str | None] = mapped_column(Text, nullable=True)


class KiLease(Base):
    """Agentenflotte: ein Host gehört während einer Analyse genau einem Agenten.

    Je Gespräch (Host) seriell, über Gespräche parallel — zwei Agenten am selben Host schrieben
    zwei Befunde, die voneinander nichts wissen. Ein Fall belegt alle seine Hosts oder keinen;
    `bis` löst den Lease eines abgestürzten Agenten (benannt abgelaufen, nicht still).
    """
    __tablename__ = "ki_lease"

    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent: Mapped[str] = mapped_column(String(64), index=True)
    fall: Mapped[str] = mapped_column(String(255))
    seit: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bis: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
