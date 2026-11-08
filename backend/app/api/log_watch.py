"""API der Log-Überwachung: Feldkatalog aller Quellen lesen, neu messen, Rolle setzen (Admin)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin, RequireSysAdmin, get_db
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


def _pruefe_entwurf(body: DefinitionBody) -> list[str]:
    """Fehler eines Entwurfs (Kriterien, Parameter, Objekte, Regeln) — eine Prüfung für Speichern und Vorschau."""
    from app.services.log_watch.definition import STANDARD_OBJEKTE, pruefe
    from app.services.log_watch.filter import FilterFehler, aufloesen, lies
    from app.services.log_watch.regelwerk import pruefe as pruefe_regeln

    fehler = pruefe(body.kriterien, body.parameter)
    objekte = body.objekte if body.objekte is not None else STANDARD_OBJEKTE
    for name, text in objekte.items():
        try:
            aufloesen(lies(text), objekte, (name,))
        except FilterFehler as e:
            fehler.append(f"Objekt @{name}: {e}")
    if body.regeln is not None:
        fehler += pruefe_regeln(body.regeln, {k.get("id") for k in body.kriterien}, objekte)
    return fehler


@router.get("/regeln/treffer", dependencies=[RequireSysAdmin])
async def get_regel_treffer(db: Annotated[AsyncSession, Depends(get_db)],
                            stunden: float = Query(24, gt=0, le=24 * 14)):
    """Regel-Editor: je Regel Treffer (entschieden), verdrängt (passte, eine frühere entschied — gemessene
    Verdeckung), Testtreffer und letzter Treffer im Zeitraum, aus den Rasterläufen."""
    from datetime import datetime, timedelta, timezone

    from app.models.log_watch import RasterLauf
    from app.services.log_watch.regel_editor import treffer_je_regel
    seit = datetime.now(timezone.utc) - timedelta(hours=stunden)
    rows = (await db.execute(select(RasterLauf).where(RasterLauf.fenster_start >= seit))).scalars()
    return {"stunden": stunden, "regeln": treffer_je_regel([(r.quelle, r.fenster_start, r.werte) for r in rows])}


@router.post("/definition/pruefen", dependencies=[RequireSysAdmin])
async def pruefe_definition(body: DefinitionBody, db: Annotated[AsyncSession, Depends(get_db)],
                            stunden: float = Query(24, gt=0, le=24 * 7)):
    """Entwurf prüfen, ohne zu speichern: Fehler und Vorschau, welche Urteile der gespeicherten Nachrichten
    im Zeitraum sich ändern würden (Commit-Vorschau des Regel-Editors)."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.definition import STANDARD_OBJEKTE, aktuelle
    from app.services.log_watch.historie import PRAEFIX
    from app.services.log_watch.regel_editor import VORSCHAU_MAX, vorschau
    from app.services.log_watch.regelwerk import aus_daten

    fehler = _pruefe_entwurf(body)
    if fehler or body.regeln is None:
        return {"fehler": fehler, "vorschau": None}
    d = await aktuelle(db)
    neu = aus_daten(body.regeln, body.objekte if body.objekte is not None else STANDARD_OBJEKTE)
    res = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": VORSCHAU_MAX, "sort": [{"zeit": "desc"}],
        "query": {"bool": {"filter": [{"range": {"zeit": {"gte": f"now-{int(stunden * 60)}m"}}}],
                           "must_not": [{"terms": {"quelle": ["ki", "konsole"]}}]}},
        "_source": ["hashtags", "level", "relevanz", "herkunft", "text", "host"]})
    docs = [{**h["_source"], "_id": h["_id"]} for h in res["hits"]["hits"]]
    return {"fehler": [], "vorschau": {**vorschau(d.regeln, neu, docs), "stunden": stunden,
                                       "gekappt": len(docs) >= VORSCHAU_MAX}}


# Nutzer 07.11.2026: auch sysadmin übernimmt Regelwerk-Versionen (jede mit Begründung, Urheber im Audit)
@router.post("/definition", dependencies=[RequireSysAdmin])
async def neue_definition(body: DefinitionBody, user: CurrentUser,
                          db: Annotated[AsyncSession, Depends(get_db)]):
    """Neue Version anlegen. Jede Änderung braucht eine Begründung; nichts wird überschrieben."""
    from datetime import datetime, timezone

    from app.models.log_watch import RelevanzDefinition
    from app.services.log_watch.definition import aktuelle

    fehler = _pruefe_entwurf(body)
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
            "widersprueche": stufe0.widersprueche(regeln, THEMEN),
            # der von Hand eingespielte Themen-Ausdruck: noch gleich dem Vokabular?
            "themen_ausdruck": stufe0.themen_ausdruck_stand(regeln, THEMEN)}


@router.get("/suche")
async def suche_historie(_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)],
                         filter: str = Query("", max_length=2000), stunden: float = Query(24, gt=0, le=24 * 30),
                         limit: int = Query(20, ge=0, le=200), bedeutung: str = Query("", max_length=500)):
    """Eine Suche über die Historie: Treffer + Facetten (dieselbe Sicht wie die KI-Werkzeuge)."""
    from app.services.log_watch import suche
    from app.services.log_watch.definition import aktuelle
    return await suche.suche(filter, stunden=stunden, limit=limit, bedeutung=bedeutung,
                             objekte=(await aktuelle(db)).objekte)


@router.get("/netzplan")
async def netzplan_historie(_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)],
                            filter: str = Query("", max_length=2000), stunden: float = Query(24, gt=0, le=24 * 30),
                            knoten: int = Query(15, ge=2, le=40),
                            sicht: str = Query("alles", pattern="^(alles|fehler)$")):
    """Netzplan zum Filter: Knoten = Hashtags, Kante = gemeinsam an derselben Nachricht. `sicht=fehler`:
    Problemobjekte (Hosts, Befunde, Themen …) nach „dringend und unbearbeitet“ statt Kontext-Tags nach Menge."""
    from app.services.log_watch import suche
    from app.services.log_watch.definition import aktuelle
    return await suche.netzplan(filter, stunden=stunden, knoten=knoten, objekte=(await aktuelle(db)).objekte,
                                sicht=sicht)


# ── Bekannte Fehler: was die KI schon untersucht hat (bekannt.py) ─────────────

@router.get("/bekannt")
async def bekannte_fehler(_user: CurrentUser, status: str = Query("", pattern="^(|aktiv|widerlegt)$"),
                          suche: str = Query("", max_length=200), limit: int = Query(100, ge=0, le=500)):
    """Bekannte Fehler als Objekte (Filter, Host, Hashtags, Befund, Wiederholungen ohne KI)."""
    from app.services.log_watch import bekannt
    return await bekannt.liste(status or None, suche, limit)


class VergessenBody(BaseModel):
    grund: str


@router.post("/bekannt/{eintrag_id}/vergessen", dependencies=[RequireSysAdmin])
async def bekannten_fehler_vergessen(eintrag_id: str, body: VergessenBody, user: CurrentUser,
                                     db: Annotated[AsyncSession, Depends(get_db)]):
    """Ein Mensch widerlegt das Urteil: beim nächsten Auftreten analysiert die KI neu. Grund ist Pflicht."""
    from app.services.log_watch import bekannt
    if not body.grund.strip():
        raise HTTPException(422, "Grund fehlt")
    e = await bekannt.vergiss(eintrag_id, body.grund.strip(), user.email)
    if e is None:
        raise HTTPException(404, "kein bekannter Fehler mit dieser ID")
    db.add(AuditLog(action="log_bekannt_vergessen", resource_type="log_bekannt", resource_id=eintrag_id,
                    user_id=user.id, new_value={"fehler": e.get("fehler"), "befund": e.get("befund"),
                                                "grund": body.grund.strip()[:1000]}))
    await db.commit()
    return e



# ── Nachricht als Objekt (nachricht.py): lesen und über Methoden ändern ──────

@router.get("/nachrichten/{nachricht_id}")
async def nachricht_lesen(nachricht_id: str, _user: CurrentUser):
    """Das Objekt: feste Felder, änderbare Felder, Verknüpfungen, Geschichte der Änderungen."""
    from app.services.log_watch import nachricht
    n = await nachricht.lies(nachricht_id)
    if n is None:
        raise HTTPException(404, "keine Nachricht mit dieser ID")
    return n


class UrteilBody(BaseModel):
    urteil: str
    relevanz: int
    kriterien: list[str]
    grund: str


class HashtagBody(BaseModel):
    hinzu: list[str] = []
    weg: list[str] = []
    grund: str


class AendernBody(BaseModel):
    """Eine Methode auf das Ergebnis eines Filters (Objekte sind Suchergebnisse)."""
    filter: str
    methode: str                     # urteil | hashtags | neu_bewerten
    grund: str
    urteil: str | None = None
    relevanz: int | None = None
    kriterien: list[str] | None = None
    hinzu: list[str] = []
    weg: list[str] = []
    stunden: float | None = None


def _ablehnung(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


def _grund(grund: str, user) -> str:
    """Grund ist Pflicht — VOR dem Anhängen des Urhebers geprüft (sonst wäre „ (mail)“ ein Grund)."""
    if not (grund or "").strip():
        raise HTTPException(422, "Grund ist Pflicht — jede Änderung braucht einen zureichenden Grund")
    return f"{grund.strip()} ({user.email})"


async def _audit(db, user, aktion: str, ziel: str, werte: dict) -> None:
    db.add(AuditLog(action=aktion, resource_type="log_nachricht", resource_id=ziel[:200], user_id=user.id,
                    new_value=werte))
    await db.commit()


@router.post("/nachrichten/{nachricht_id}/urteil", dependencies=[RequireSysAdmin])
async def nachricht_urteilen(nachricht_id: str, body: UrteilBody, user: CurrentUser,
                             db: Annotated[AsyncSession, Depends(get_db)]):
    """Ein Mensch urteilt — Vorrang vor KI und Regel; Grund ist Pflicht, die Änderung steht an der Nachricht.
    Betriebsarbeit wie Quittieren: admin oder sysadmin. Massenänderung über einen Filter bleibt admin."""
    from app.services.log_watch import nachricht
    try:
        r = await nachricht.urteile(body.urteil, body.relevanz, body.kriterien, "mensch",
                                    _grund(body.grund, user), ids=[nachricht_id])
    except nachricht.NachrichtAbgelehnt as e:
        raise _ablehnung(e)
    if not r:
        raise HTTPException(404, "keine Nachricht mit dieser ID")
    await _audit(db, user, "log_nachricht_urteil", nachricht_id, body.model_dump())
    return {"ergebnis": r, "nachricht": await nachricht.lies(nachricht_id)}


@router.post("/nachrichten/{nachricht_id}/hashtags", dependencies=[RequireSysAdmin])
async def nachricht_hashtags(nachricht_id: str, body: HashtagBody, user: CurrentUser,
                             db: Annotated[AsyncSession, Depends(get_db)]):
    """Hashtags hinzufügen/entfernen (nicht quelle:/host: — die spiegeln feste Felder)."""
    from app.services.log_watch import nachricht
    try:
        r = await nachricht.markiere(body.hinzu, body.weg, "mensch", _grund(body.grund, user),
                                     ids=[nachricht_id])
    except nachricht.NachrichtAbgelehnt as e:
        raise _ablehnung(e)
    if not r:
        raise HTTPException(404, "keine Nachricht mit dieser ID")
    await _audit(db, user, "log_nachricht_hashtags", nachricht_id, body.model_dump())
    return {"ergebnis": r, "nachricht": await nachricht.lies(nachricht_id)}


@router.post("/nachrichten/aendern", dependencies=[RequireAdmin])
async def nachrichten_aendern(body: AendernBody, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    """Dieselbe Methode auf alle Nachrichten eines Filters. Leerer Filter ist abgelehnt."""
    from app.services.log_watch import filter as flt
    from app.services.log_watch import nachricht
    from app.services.log_watch.definition import aktuelle
    objekte = (await aktuelle(db)).objekte
    grund = _grund(body.grund, user)
    try:
        if body.methode == "urteil":
            if body.urteil is None or body.relevanz is None or not body.kriterien:
                raise nachricht.NachrichtAbgelehnt("urteil, relevanz und kriterien sind Pflicht")
            n = await nachricht.urteile(body.urteil, body.relevanz, body.kriterien, "mensch", grund,
                                        filtertext=body.filter, objekte=objekte)
            erg = {"geaendert": n}
        elif body.methode == "hashtags":
            erg = {"geaendert": await nachricht.markiere(body.hinzu, body.weg, "mensch", grund,
                                                         filtertext=body.filter, objekte=objekte)}
        elif body.methode == "neu_bewerten":
            erg = await nachricht.neu_bewerten(body.filter, "mensch", grund, objekte=objekte, stunden=body.stunden)
        else:
            raise nachricht.NachrichtAbgelehnt("methode muss urteil, hashtags oder neu_bewerten sein")
    except (nachricht.NachrichtAbgelehnt, flt.FilterFehler) as e:
        raise _ablehnung(e)
    await _audit(db, user, f"log_nachrichten_{body.methode}", body.filter, {**body.model_dump(), **erg})
    return erg
