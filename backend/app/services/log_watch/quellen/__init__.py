"""Quell-Adapter der Log-Überwachung — eine Schnittstelle für Graylog, Wazuh und CheckMK.

Jeder Adapter liefert:
  name, fenster                        Quellname und lesbares Messfenster
  felder()  -> list[Feld]              alle Felder mit Typ
  messe(felder) -> (gesamt, stats)     Nachrichten im Fenster und je Feld
                                        {mit_feld, kardinalitaet, beispielwerte?}

Felder, die die Quelle nicht messen kann, fehlen in `stats` (nicht gemessen ≠ leer).
"""
from __future__ import annotations

from typing import Protocol

from app.services.log_watch.catalog import Feld

QUELLEN = ("graylog", "wazuh", "checkmk")


class Quelle(Protocol):
    name: str
    fenster: str

    async def felder(self) -> list[Feld]: ...

    async def messe(self, felder: list[Feld]) -> tuple[int, dict[str, dict]]: ...


async def lade_quellen(db) -> tuple[list[Quelle], dict[str, str]]:
    """Adapter für jeden aktiven Connector. Rückgabe: (Adapter, {quelle: Grund für Fehlen})."""
    from sqlalchemy import select

    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig

    rows = (await db.execute(select(ConnectorConfig).where(
        ConnectorConfig.enabled.is_(True), ConnectorConfig.type.in_(QUELLEN)))).scalars().all()
    erste = {}
    for c in rows:
        erste.setdefault(c.type, c)
    adapter: list[Quelle] = []
    fehlend: dict[str, str] = {}
    for name in QUELLEN:
        c = erste.get(name)
        if c is None:
            fehlend[name] = "kein aktiver Connector"
            continue
        creds = decrypt_credentials(c.encrypted_credentials)
        if name == "graylog":
            from app.services.connectors.graylog import GraylogConnector
            from app.services.log_watch.quellen.graylog import GraylogQuelle
            adapter.append(GraylogQuelle(GraylogConnector(base_url=c.base_url, credentials=creds)))
        elif name == "wazuh":
            from app.services.connectors.wazuh import WazuhConnector
            from app.services.log_watch.quellen.wazuh import WazuhQuelle
            w = WazuhConnector(base_url=c.base_url, credentials=creds)
            if not w._indexer_base():
                fehlend[name] = "Wazuh-Connector ohne indexer_url"
                continue
            adapter.append(WazuhQuelle(w))
        else:
            from app.services.connectors.checkmk import CheckMKConnector
            from app.services.log_watch.quellen.checkmk import CheckMKQuelle
            adapter.append(CheckMKQuelle(CheckMKConnector(base_url=c.base_url, credentials=creds)))
    return adapter, fehlend
