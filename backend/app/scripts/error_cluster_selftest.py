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
