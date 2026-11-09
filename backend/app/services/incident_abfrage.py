"""Incidents für die Konsole abfragen — und prüfen, ob zwei Incidents zusammengehören (Nutzer 09.11.2026:
„die Konsole kann die Incidents nicht abfragen. Das wäre wichtig, wenn zwei Incidents zusammengehören.“).

Nur lesend. Dieselben Daten wie die Weboberfläche (GET /feed/incidents, …/timeline): Tabelle `incidents`
(Titel, Haupthost, Schweregrad, Status, Art, causal_context) und `incident_members` (Meldungen per external_id).

Zusammengehörigkeit (find_related) — nur mit benanntem, gemeinsamem Merkmal, nie aus zeitlicher Nähe allein
(kein post hoc, CLAUDE.md Regel 5):
- gleicher_host   ein Host steht in beiden Incidents (Haupthost oder in causal_context[].hosts)
- gleiche_meldung beide enthalten dieselbe Meldung (incident_members.external_id)
Die zeitliche Überlappung wird nur als Filter (Suchfenster) und als Kontext genannt, nicht als Grund.
Ob die Zusammengehörigkeit ursächlich ist, sagt das nicht — das prüft der Mensch bzw. der Agent mit Belegen.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone

#: Status eines Incidents (erschöpfend, wie models/workflow.Incident.status) und die Filterwörter dafür
STATUS = ("open", "investigating", "resolved")
STATUS_FILTER = {"offen": ("open", "investigating"), "alle": STATUS, **{s: (s,) for s in STATUS}}
GRUENDE = {"gleicher_host": "gleicher Host", "gleiche_meldung": "dieselbe Meldung"}


def _ist_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def host_schluessel(name: str) -> str:
    """Vergleichsform eines Hostnamens: FQDN klein ohne Schlusspunkt. Rein."""
    return (name or "").strip().strip("[]").rstrip(".").lower()


def gleicher_host(a: str, b: str) -> bool:
    """Gleich, wenn die FQDNs gleich sind — oder eine Seite nur einen Kurznamen hat und der passt.
    Zwei verschiedene Domains mit gleichem Kurznamen (web01.example.com / web01.example.net) sind NICHT gleich. Rein."""
    a, b = host_schluessel(a), host_schluessel(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if _ist_ip(a) or _ist_ip(b):
        return False
    if "." in a and "." in b:
        return False
    from app.services.log_watch.hosts import norm
    return norm(a) == norm(b)


def hosts_von(inc: dict) -> list[str]:
    """Alle Hosts eines Incidents: Haupthost + causal_context[].hosts, ohne Doppel. Rein."""
    out: list[str] = []
    for h in [inc.get("primary_host") or ""] + [h for c in (inc.get("causal_context") or []) if isinstance(c, dict)
                                                for h in (c.get("hosts") or [])]:
        k = host_schluessel(str(h))
        if k and k not in out:
            out.append(k)
    return out


def _zeitraum(inc: dict, jetzt: datetime) -> tuple[datetime, datetime]:
    return inc["created_at"], inc.get("resolved_at") or jetzt


def verwandte(ziel: dict, kandidaten: list[dict], mitglieder: dict[str, set[str]], jetzt: datetime) -> list[dict]:
    """Kandidaten mit gemeinsamem Merkmal → [{incident, gruende[], zeitlich}], meiste Gründe zuerst. Rein.
    `mitglieder`: incident-id → Menge der external_ids."""
    z_hosts = hosts_von(ziel)
    z_mit = mitglieder.get(ziel["id"], set())
    z_von, z_bis = _zeitraum(ziel, jetzt)
    out = []
    for k in kandidaten:
        if k["id"] == ziel["id"]:
            continue
        gruende = []
        gemeinsam = sorted({a for a in z_hosts for b in hosts_von(k) if gleicher_host(a, b)})
        if gemeinsam:
            gruende.append({"art": "gleicher_host", "text": f"{GRUENDE['gleicher_host']}: {', '.join(gemeinsam[:5])}",
                            "hosts": gemeinsam})
        geteilt = sorted(z_mit & mitglieder.get(k["id"], set()))
        if geteilt:
            gruende.append({"art": "gleiche_meldung", "text": f"{len(geteilt)} gemeinsame Meldung(en)",
                            "external_ids": geteilt[:10]})
        if not gruende:
            continue
        k_von, k_bis = _zeitraum(k, jetzt)
        ueberlapp = min(z_bis, k_bis) - max(z_von, k_von)
        zeitlich = ("überlappen sich" if ueberlapp.total_seconds() > 0
                    else f"Abstand {abs(int(ueberlapp.total_seconds()) // 60)} min")
        out.append({"incident": k, "gruende": gruende, "zeitlich": f"{zeitlich} (Kontext, kein Grund)"})
    return sorted(out, key=lambda x: (-len(x["gruende"]), -x["incident"]["created_at"].timestamp()))


def _als_dict(inc, anzahl: int | None = None) -> dict:
    d = {"id": str(inc.id), "titel": inc.title, "primary_host": inc.primary_host, "severity": inc.severity,
         "status": inc.status, "kind": inc.kind, "created_at": inc.created_at, "updated_at": inc.updated_at,
         "resolved_at": inc.resolved_at, "causal_context": inc.causal_context or []}
    if anzahl is not None:
        d["meldungen"] = anzahl
    return d


def ausgabe(d: dict) -> dict:
    """Zeiten als ISO-Text, causal_context gekürzt (Hosts, Regel, Gründe, Triage-Titel). Rein."""
    o = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items() if k != "causal_context"}
    o["hosts"] = hosts_von(d)
    from app.services.error_clusters.bearbeitung import zustand
    o["bearbeitung"] = zustand(d.get("causal_context"))
    o["kontext"] = [{"regel": c.get("regel"), "hosts": (c.get("hosts") or [])[:10], "gruende": (c.get("reasons") or [])[:3],
                     "triage": (c.get("triage") or {}).get("titel")}
                    for c in (d.get("causal_context") or []) if isinstance(c, dict)]
    return o


async def liste(db, status: str = "offen", host: str = "", text: str = "", stunden: float = 72, limit: int = 20) -> dict:
    from sqlalchemy import Text, desc, func, or_, select
    from app.models.workflow import Incident, IncidentMember
    if status not in STATUS_FILTER:
        return {"fehler": f"status {status!r} unbekannt — erlaubt: {', '.join(STATUS_FILTER)}"}
    seit = datetime.now(timezone.utc) - timedelta(hours=max(1.0, stunden))
    stmt = select(Incident).where(Incident.status.in_(STATUS_FILTER[status]), Incident.updated_at >= seit)
    if text.strip():
        stmt = stmt.where(Incident.title.ilike(f"%{text.strip()}%"))
    if host.strip():
        h = host_schluessel(host)
        stmt = stmt.where(or_(Incident.primary_host.ilike(f"{h.split('.')[0]}%"),
                              Incident.causal_context.cast(Text).ilike(f"%{h.split('.')[0]}%")))
    rows = (await db.execute(stmt.order_by(desc(Incident.updated_at)).limit(max(1, min(limit, 100)) * (3 if host else 1)))
            ).scalars().all()
    if host.strip():   # Vorfilter per ILIKE, hier die genaue Host-Gleichheit
        rows = [r for r in rows if any(gleicher_host(host, x) for x in hosts_von(_als_dict(r)))][:limit]
    anz = dict((await db.execute(select(IncidentMember.incident_id, func.count()).where(
        IncidentMember.incident_id.in_([r.id for r in rows])).group_by(IncidentMember.incident_id))).all()) if rows else {}
    return {"filter": {"status": status, "host": host, "text": text, "stunden": stunden}, "anzahl": len(rows),
            "incidents": [ausgabe(_als_dict(r, anz.get(r.id, 0))) for r in rows]}


async def _mitglieder(db, ids: list) -> dict[str, set[str]]:
    from sqlalchemy import select
    from app.models.workflow import IncidentMember
    out: dict[str, set[str]] = {}
    if ids:
        for iid, ext in (await db.execute(select(IncidentMember.incident_id, IncidentMember.external_id).where(
                IncidentMember.incident_id.in_(ids)))).all():
            out.setdefault(str(iid), set()).add(ext)
    return out


async def details(db, incident_id: str) -> dict:
    import uuid
    from app.models.workflow import Incident
    try:
        inc = await db.get(Incident, uuid.UUID(incident_id))
    except ValueError:
        return {"fehler": f"keine gültige Incident-ID: {incident_id!r}"}
    if inc is None:
        return {"fehler": f"Incident {incident_id} nicht gefunden"}
    mit = (await _mitglieder(db, [inc.id])).get(str(inc.id), set())
    meldungen = []
    if mit:
        try:
            from app.services.feed_index import search_by_query
            q = " OR ".join(f'external_id:"{e}"' for e in sorted(mit)[:30])
            for it in await search_by_query("cs-feed-*", q, size=50):
                meldungen.append({"zeit": it.get("created_at"), "quelle": it.get("source"), "severity": it.get("severity"),
                                  "host": (it.get("metadata") or {}).get("host"), "titel": (it.get("title") or "")[:200],
                                  "external_id": it.get("external_id")})
        except Exception as e:  # noqa: BLE001 — Incident bleibt lesbar, der Ausfall ist benannt
            meldungen = [{"fehler": f"Meldungen nicht ladbar: {e}"}]
    d = _als_dict(inc, len(mit))
    o = ausgabe(d)
    o["causal_context"] = d["causal_context"]          # hier vollständig
    o["meldungen_liste"] = sorted(meldungen, key=lambda m: str(m.get("zeit") or ""))
    o["nicht_im_feed"] = max(0, len(mit) - len([m for m in meldungen if "external_id" in m]))
    o.update(belege_aus_kontext(d, len(mit)))
    return o


def belege_aus_kontext(d: dict, mitglieder: int) -> dict:
    """Incidents der Log-Überwachung haben keine Feed-Meldungen (gemessen 09.11.: 184 von 4.903 der letzten 7 Tage,
    alle mit Beispielen im Kontext) — ihre Belege sind causal_context[].examples. Benannt statt leer. Rein."""
    bsp = [str(e)[:400] for c in (d.get("causal_context") or []) if isinstance(c, dict) for e in (c.get("examples") or [])]
    if mitglieder:
        return {}
    return {"belege_aus_kontext": bsp[:20],
            "hinweis": ("keine verknüpften Feed-Meldungen — Incident aus der Log-Überwachung; Belege sind die "
                        "Beispielzeilen aus causal_context (belege_aus_kontext)") if bsp else
                       "weder verknüpfte Meldungen noch Beispielzeilen im Kontext"}


async def verwandte_suchen(db, incident_id: str, stunden: float = 24, limit: int = 10) -> dict:
    import uuid
    from sqlalchemy import and_, or_, select
    from app.models.workflow import Incident
    try:
        inc = await db.get(Incident, uuid.UUID(incident_id))
    except ValueError:
        return {"fehler": f"keine gültige Incident-ID: {incident_id!r}"}
    if inc is None:
        return {"fehler": f"Incident {incident_id} nicht gefunden"}
    jetzt = datetime.now(timezone.utc)
    rand = timedelta(hours=max(0.0, stunden))
    von, bis = inc.created_at - rand, (inc.resolved_at or jetzt) + rand
    kand = (await db.execute(select(Incident).where(and_(
        Incident.created_at <= bis, or_(Incident.resolved_at.is_(None), Incident.resolved_at >= von),
        Incident.id != inc.id)).limit(2000))).scalars().all()
    mit = await _mitglieder(db, [inc.id] + [k.id for k in kand])
    ziel = _als_dict(inc)
    treffer = verwandte(ziel, [_als_dict(k) for k in kand], mit, jetzt)[:max(1, min(limit, 50))]
    return {"incident": ausgabe(ziel), "suchfenster": {"von": von.isoformat(), "bis": bis.isoformat(), "stunden_rand": stunden},
            "geprueft": len(kand),
            "regel": "verwandt nur bei gemeinsamem Host oder gemeinsamer Meldung; zeitliche Nähe allein zählt nicht",
            "verwandte": [{**ausgabe(t["incident"]), "gruende": t["gruende"], "zeitlich": t["zeitlich"]} for t in treffer]}
