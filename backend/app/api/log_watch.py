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


@router.get("/raster/trockenlauf", dependencies=[RequireAdmin])
async def raster_trockenlauf(db: Annotated[AsyncSession, Depends(get_db)],
                             stunden: int = Query(24, ge=1, le=24 * 7)):
    """Was das Raster in den letzten `stunden` gespeichert HÄTTE, je Quelle und pro Tag."""
    from datetime import datetime, timedelta, timezone

    from app.models.log_watch import RasterLauf
    from app.services.log_watch.lauf import zusammenfassung

    seit = datetime.now(timezone.utc) - timedelta(hours=stunden)
    rows = (await db.execute(select(RasterLauf).where(RasterLauf.fenster_start >= seit))).scalars()
    return zusammenfassung([(r.quelle, r.fenster_start, r.werte) for r in rows], stunden)


# ── Relevanz-Definition ───────────────────────────────────────────────────────

@router.get("/definition")
async def get_definition(_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)],
                         alle: bool = Query(False)):
    """Aktuelle Definition (oder alle Versionen): was relevant ist und was nicht."""
    from app.models.log_watch import RelevanzDefinition
    from app.services.log_watch.definition import aktuelle

    from app.services.log_watch.regelwerk import als_daten as regeln_als_daten

    d = await aktuelle(db)
    if not alle:
        return {"version": d.version, "kriterien": d.kriterien, "parameter": d.parameter,
                "objekte": d.objekte, "regeln": regeln_als_daten(d.regeln),
                "default": {"aktion": "block", "kriterium": "N0"}, "code_neuer": d.code_neuer}
    rows = (await db.execute(select(RelevanzDefinition)
                             .order_by(RelevanzDefinition.version.desc()))).scalars()
    return [{"version": r.version, "kriterien": r.kriterien, "parameter": r.parameter,
             "begruendung": r.begruendung, "erstellt_von": r.erstellt_von,
             "erstellt_at": r.erstellt_at} for r in rows]


class DefinitionBody(BaseModel):
    kriterien: list[dict]
    parameter: dict
    begruendung: str
    #: Regelwerk; fehlt es, gelten die Standardregeln
    regeln: list[dict] | None = None
    #: Objekte (Name → Filtertext); fehlen sie, gelten die Standardobjekte
    objekte: dict[str, str] | None = None


@router.post("/definition", dependencies=[RequireAdmin])
async def neue_definition(body: DefinitionBody, user: CurrentUser,
                          db: Annotated[AsyncSession, Depends(get_db)]):
    """Neue Version anlegen. Jede Änderung braucht eine Begründung; nichts wird überschrieben."""
    from datetime import datetime, timezone

    from app.models.log_watch import RelevanzDefinition
    from app.services.log_watch.definition import aktuelle, pruefe

    from app.services.log_watch.regelwerk import pruefe as pruefe_regeln

    fehler = pruefe(body.kriterien, body.parameter)
    from app.services.log_watch.definition import STANDARD_OBJEKTE
    from app.services.log_watch.filter import FilterFehler, aufloesen, lies

    objekte = body.objekte if body.objekte is not None else STANDARD_OBJEKTE
    for name, text in objekte.items():
        try:
            aufloesen(lies(text), objekte, (name,))
        except FilterFehler as e:
            fehler.append(f"Objekt @{name}: {e}")
    if body.regeln is not None:
        fehler += pruefe_regeln(body.regeln, {k.get("id") for k in body.kriterien}, objekte)
    if not body.begruendung.strip():
        fehler.append("Begründung fehlt")
    if fehler:
        raise HTTPException(422, "; ".join(fehler))
    alt = await aktuelle(db)
    row = RelevanzDefinition(version=alt.version + 1, kriterien=body.kriterien,
                             parameter=body.parameter, regeln=body.regeln, objekte=body.objekte,
                             begruendung=body.begruendung[:1000],
                             erstellt_von=user.email, erstellt_at=datetime.now(timezone.utc))
    db.add(row)
    db.add(AuditLog(action="log_relevanz_definition", resource_type="relevanz_definition",
                    resource_id=str(row.version), user_id=user.id,
                    new_value={"version": row.version, "begruendung": body.begruendung}))
    await db.commit()
    return {"version": row.version}


@router.get("/stufe0", dependencies=[RequireAdmin])
async def get_stufe0(db: Annotated[AsyncSession, Depends(get_db)]):
    """Stufe 0: Graylog-Verwerfregeln (live, mit Quelltext) und die letzte Zählung je Regel."""
    from app.models.log_watch import RasterLauf
    from app.services.log_watch import stufe0
    from app.services.log_watch.quellen import lade_quellen

    quellen, fehlend = await lade_quellen(db)
    gl = next((q.gl for q in quellen if q.name == "graylog"), None)
    if gl is None:
        raise HTTPException(503, f"Graylog nicht verfügbar: {fehlend.get('graylog', '?')}")
    regeln, knoten = await stufe0.lies(gl)
    letzte = (await db.execute(select(RasterLauf).where(RasterLauf.quelle == "stufe0")
                               .order_by(RasterLauf.fenster_start.desc()).limit(1))).scalar_one_or_none()
    zaehlung = {r["titel"]: r for r in ((letzte.werte or {}).get("regeln", []) if letzte else [])}
    from app.services.log_watch.vokabular import THEMEN
    return {"knoten": sorted(knoten), "gemessen": letzte.fenster_start if letzte else None,
            "regeln": [{**r, "zaehlung": zaehlung.get(r["titel"])} for r in regeln],
            "widersprueche": stufe0.widersprueche(regeln, THEMEN)}
