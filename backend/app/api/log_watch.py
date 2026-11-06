"""API der Log-Überwachung: Feldkatalog aller Quellen lesen, neu messen, Rolle setzen (Admin)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin, get_db
from app.models.audit import AuditLog
from app.models.log_watch import FieldCatalog
from app.services.log_watch import catalog
from app.services.log_watch.quellen import QUELLEN

router = APIRouter(prefix="/log-watch", tags=["log-watch"])

_QUELLE = "^(" + "|".join(QUELLEN) + ")$"


def _zeile(r: FieldCatalog) -> dict:
    return {"quelle": r.quelle, "feld": r.feld, "typ": r.typ, "numerisch": r.numerisch,
            "rolle": r.rolle, "rolle_herkunft": r.rolle_herkunft, "rolle_grund": r.rolle_grund,
            "status": r.status, "kardinalitaet": r.kardinalitaet, "mit_feld": r.mit_feld,
            "abdeckung": r.abdeckung, "beispielwerte": r.beispielwerte or [],
            "first_seen": r.first_seen, "last_seen": r.last_seen, "gemessen_at": r.gemessen_at}


@router.get("/fields", dependencies=[RequireAdmin])
async def list_fields(
    db: Annotated[AsyncSession, Depends(get_db)],
    quelle: str | None = Query(None, pattern=_QUELLE),
    rolle: str | None = Query(None),
    status: str | None = Query(None, pattern="^(aktiv|ohne_werte|nicht_gemessen|verschwunden)$"),
):
    """Katalog, häufigste Felder zuerst, mit Zählung je Quelle × Rolle und Status."""
    q = select(FieldCatalog)
    if quelle:
        q = q.where(FieldCatalog.quelle == quelle)
    rows = list((await db.execute(q)).scalars())
    zaehlung: dict[str, dict] = {}
    for r in rows:
        z = zaehlung.setdefault(r.quelle, {"je_rolle": {}, "je_status": {}, "gemessen_at": None})
        z["je_rolle"][r.rolle] = z["je_rolle"].get(r.rolle, 0) + 1
        z["je_status"][r.status] = z["je_status"].get(r.status, 0) + 1
        if r.gemessen_at and (z["gemessen_at"] is None or r.gemessen_at > z["gemessen_at"]):
            z["gemessen_at"] = r.gemessen_at
    if rolle:
        rows = [r for r in rows if r.rolle == rolle]
    if status:
        rows = [r for r in rows if r.status == status]
    rows.sort(key=lambda r: (r.quelle, -(r.abdeckung or 0), r.feld))
    return {"quellen": zaehlung, "felder": [_zeile(r) for r in rows]}


@router.post("/fields/refresh", dependencies=[RequireAdmin])
async def refresh_fields(db: Annotated[AsyncSession, Depends(get_db)],
                         quelle: str | None = Query(None, pattern=_QUELLE)):
    """Feldlisten neu holen und messen (sonst täglich). Ergebnis je Quelle, Fehler mit Grund."""
    return await catalog.aktualisiere(db, nur=quelle)


class RolleBody(BaseModel):
    rolle: str
    grund: str = ""


async def _feld(db: AsyncSession, quelle: str, feld: str) -> FieldCatalog:
    r = (await db.execute(select(FieldCatalog).where(
        FieldCatalog.quelle == quelle, FieldCatalog.feld == feld))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "Feld nicht im Katalog")
    return r


@router.put("/fields/{quelle}/{feld:path}/rolle", dependencies=[RequireAdmin])
async def set_rolle(quelle: str, feld: str, body: RolleBody, user: CurrentUser,
                    db: Annotated[AsyncSession, Depends(get_db)]):
    """Rolle eines Feldes festlegen; eine Messung überschreibt sie danach nicht mehr."""
    if body.rolle not in catalog.ROLLEN or body.rolle == "unbekannt":
        raise HTTPException(422, f"Rolle muss eine von {', '.join(catalog.ROLLEN[:-1])} sein")
    r = await _feld(db, quelle, feld)
    alt = r.rolle
    r.rolle, r.rolle_herkunft = body.rolle, "admin"
    r.rolle_grund = (body.grund or f"gesetzt von {user.email}")[:255]
    db.add(AuditLog(action="log_field_role_set", resource_type="field_catalog",
                    resource_id=f"{quelle}:{feld}"[:100], user_id=user.id,
                    new_value={"quelle": quelle, "feld": feld, "rolle": body.rolle,
                               "vorher": alt, "grund": body.grund}))
    await db.commit()
    return _zeile(r)


@router.delete("/fields/{quelle}/{feld:path}/rolle", dependencies=[RequireAdmin])
async def reset_rolle(quelle: str, feld: str, user: CurrentUser,
                      db: Annotated[AsyncSession, Depends(get_db)]):
    """Admin-Rolle zurücknehmen: ab jetzt gilt wieder die Regel."""
    r = await _feld(db, quelle, feld)
    r.rolle_herkunft = "regel"
    r.rolle, r.rolle_grund = catalog.bestimme_rolle(r.feld, r.numerisch, r.kardinalitaet,
                                                    r.mit_feld)
    db.add(AuditLog(action="log_field_role_reset", resource_type="field_catalog",
                    resource_id=f"{quelle}:{feld}"[:100], user_id=user.id,
                    new_value={"quelle": quelle, "feld": feld, "rolle": r.rolle}))
    await db.commit()
    return _zeile(r)
