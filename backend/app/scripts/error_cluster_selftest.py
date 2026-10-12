"""Selbsttest des Incident-Lebenszyklus gegen das echte Postgres — alles wird zurückgerollt.

Die Unit-Tests laufen ohne Datenbank (conftest ersetzt die Modelle). Was dort nicht
prüfbar ist — SQL, JSONB-Kontext, Mitglieder, Zusammenführung — prüft dieses Skript in
einer Transaktion, die am Ende verworfen wird. Es bleibt nichts zurück.

    docker compose run --rm --no-deps backend python -m app.scripts.error_cluster_selftest
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.workflow import Incident, IncidentMember
from app.services.error_clusters.incident import (
    _host_incident, add_pattern, close_cluster_incident, cluster_context, open_cluster_incident,
)

HOST = f"selftest-{uuid.uuid4().hex[:6]}.example.invalid"
CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, ok))
    print(f"  {'OK  ' if ok else 'FEHL'} {name}")


async def members(db, inc_id) -> set[str]:
    rows = await db.execute(select(IncidentMember.external_id)
                            .where(IncidentMember.incident_id == inc_id))
    return {r[0] for r in rows}


async def fleet_section(db, now) -> None:
    from datetime import timedelta
    from app.models.alert import Alert
    from app.models.error_cluster import ErrorCluster
    from app.services.error_clusters import fleet
    tag = uuid.uuid4().hex[:6]
    # 0. Junger Ausfall (5 min, z. B. ein Neustart): noch kein Incident, keine Mail.
    young_svc, young_hosts = f"Selftest Reboot {tag}", [f"st{tag}-y{i}.example.invalid" for i in range(6)]
    for h in young_hosts:
        db.add(Alert(source="checkmk", severity="high", title=f"{h} {young_svc}",
                     external_id=f"st:{tag}:{h}:young", status="new",
                     metadata_={"service": young_svc, "host": h},
                     created_at=now - timedelta(minutes=5)))
    await db.flush()
    young = [n for n in await fleet.run(db, None, "") if n["ctx"].get("service") == young_svc]
    young_eps = (await db.execute(select(ErrorCluster).where(
        ErrorCluster.key == fleet.fleet_key(young_svc)))).scalars().all()
    check("Flotte: 5 min junger Ausfall → kein Incident, keine Mail", not young and not young_eps)

    svcs = [f"Selftest ORD1 {tag}", f"Selftest ORD2 {tag}"]
    hosts = [f"st{tag}-{i}.example.invalid" for i in range(6)]
    for svc in svcs:
        for h in hosts:
            db.add(Alert(source="checkmk", severity="high", title=f"{h} {svc}",
                         external_id=f"st:{tag}:{h}:{svc}", status="new",
                         # 35 min alt und noch offen: erst dann zählt ein Problem
                         # (rules.min_age_minutes).
                         metadata_={"service": svc, "host": h},
                         created_at=now - timedelta(minutes=35)))
    await db.flush()
    keys = [fleet.fleet_key(s) for s in svcs]

    async def eps():
        return (await db.execute(select(ErrorCluster).where(ErrorCluster.key.in_(keys)))
                ).scalars().all()

    out = [n for n in await fleet.run(db, None, "") if n["ctx"].get("service") in svcs]
    await db.flush()
    first = await eps()
    check("Flotte: zwei gleichzeitige Services auf denselben Hosts → eine Mail", len(out) == 1)
    check("Flotte: beide Episoden im selben Incident",
          len(first) == 2 and len({e.incident_id for e in first}) == 1
          and first[0].incident_id is not None)
    inc = (await db.execute(select(Incident).where(Incident.id == first[0].incident_id))).scalar_one()
    check("Flotte: zweiter Service als weiteres Muster im Kontext",
          len((cluster_context(inc) or {}).get("patterns", [])) == 1)

    # Beide still → schließen; dann kehrt der Ausfall 30 min später zurück.
    for e in first:
        e.status, e.closed_at = "closed", now - timedelta(minutes=30)
    await close_cluster_incident(db, str(first[0].incident_id), "selftest")
    await db.flush()
    again = [n for n in await fleet.run(db, None, "") if n["ctx"].get("service") in svcs]
    await db.flush()
    second = await eps()
    await db.refresh(inc)
    check("Flotte: Rückkehr binnen Cooldown → keine neue Mail", again == [])
    check("Flotte: Rückkehr setzt die alten Episoden fort",
          len(second) == 2 and {e.id for e in second} == {e.id for e in first}
          and all(e.status == "open" for e in second))
    check("Flotte: Incident wieder offen, Titel des Leitvorfalls erhalten",
          inc.status == "open" and "ORD1" in inc.title)


async def main() -> None:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        trans = await db.begin()
        try:
            # 1. host-Incident: ein Alert allein ist keiner, zwei sind einer
            none = await _host_incident(HOST, [{"external_id": "st:a1", "severity": "high",
                                               "source": "checkmk"}], now - timedelta(minutes=30),
                                        now, db)
            check("ein einzelner Alert eröffnet keinen Incident", none is None)
            host_inc = await _host_incident(HOST, [
                {"external_id": "st:a1", "severity": "high", "source": "checkmk"},
                {"external_id": "st:a2", "severity": "critical", "source": "graylog"}],
                now - timedelta(minutes=30), now, db)
            await db.flush()
            check("zwei Alerts → host-Incident", host_inc is not None and host_inc.kind == "host")
            check("Titel nennt Host und Quellen", HOST in host_inc.title and "checkmk" in host_inc.title)

            # 2. übergreifender Cluster auf demselben Host: host-Incident geht darin auf
            ctx = {"cluster_id": "st", "template": "selftest pattern <*>", "hosts": [HOST],
                   "docker_hosts": [HOST], "containers": [{"name": HOST, "count": 5}]}
            cl = await open_cluster_incident(db, kind="log_cluster", title="Fehlerausbruch: selftest",
                                             severity="high", primary_host=HOST, context=ctx,
                                             member_docs=[{"external_id": "ecl:st", "source": "graylog"}])
            await db.flush()
            await db.refresh(host_inc)
            check("Cluster-Incident angelegt (kind=log_cluster)", cl.kind == "log_cluster")
            check("host-Incident aufgegangen (resolved + Verweis)",
                  host_inc.status == "resolved" and any(
                      isinstance(e, dict) and e.get("type") == "merged_into"
                      for e in (host_inc.causal_context or [])))
            check("Mitglieder übernommen", {"st:a1", "st:a2", "ecl:st"} <= await members(db, cl.id))
            check("Cluster-Kontext gespeichert", (cluster_context(cl) or {}).get("template") == ctx["template"])

            # 3. weiteres Muster desselben Ausbruchs
            await add_pattern(db, str(cl.id), {"template_key": "k2", "template": "zweites <*>",
                                               "hosts": [HOST]},
                              [{"external_id": "ecl:st2", "source": "graylog"}])
            await db.flush()
            await db.refresh(cl)
            check("weiteres Muster im Kontext", len((cluster_context(cl) or {}).get("patterns", [])) == 1)
            check("weiteres Muster ist Mitglied", "ecl:st2" in await members(db, cl.id))

            # 4. schließen und wiederöffnen
            await close_cluster_incident(db, str(cl.id), "selftest")
            await db.flush()
            await db.refresh(cl)
            check("schließen → resolved mit Begründung",
                  cl.status == "resolved" and (cluster_context(cl) or {}).get("resolved_reason") == "selftest")
            again = await open_cluster_incident(db, kind="log_cluster", title="Fehlerausbruch: selftest",
                                                severity="critical", primary_host=HOST, context=ctx,
                                                member_docs=[], existing_id=str(cl.id))
            await db.flush()
            check("wiederöffnen → derselbe Incident, open", again.id == cl.id and again.status == "open")
            # 5. Flotte: zwei Services auf denselben Hosts → EIN Incident, EINE Mail;
            #    Rückkehr binnen Cooldown → alte Episode, keine neue Mail.
            await fleet_section(db, now)
        finally:
            await trans.rollback()
    leftover = None
    async with AsyncSessionLocal() as db:
        leftover = (await db.execute(select(Incident).where(Incident.primary_host == HOST))).first()
    check("nichts zurückgeblieben (Rollback)", leftover is None)
    ok = all(c for _, c in CHECKS)
    print("\nERGEBNIS:", "BESTANDEN" if ok else "NICHT BESTANDEN", f"({sum(c for _, c in CHECKS)}/{len(CHECKS)})")


if __name__ == "__main__":
    asyncio.run(main())
