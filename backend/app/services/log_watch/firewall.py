"""Firewall (Palo Alto) im Raster: Bedrohungen einzeln, Verkehr nur gezählt.

Gemessen 06.11.: ca. 2,9 Mio Meldungen/h (end/allow 2,07 Mio, URL-Alert 525 000, drop
257 000, deny 74 000), aber nur ca. 4 500 Bedrohungen/h (Spyware), davon 18 Sinkholes.
Verkehr Zeile für Zeile zu lesen wäre sinnlos — er wird je Fenster nach Feldern gezählt
(Graylog-Pivot). Bedrohungen werden gelesen und je (Bedrohung × betroffener Host ×
Aktion) gruppiert; das Regelwerk entscheidet wie bei jeder Quelle.

Betroffener Host: die Seite in einer INTERNEN Zone. Kommt die Verbindung aus einer externen
Zone (UNTRUST), ist das Ziel betroffen (Angriff von außen), sonst die Quelle (ein interner
Rechner fragt eine Schadsoftware-Domain an). Die Gegenseite steht als `gegenstelle:`.
Die Richtung `threat_direction` taugt dafür nicht: bei einem Angriff von außen ist der
Angreifer der Client (gemessen 06.11.: RCE UNTRUST → WEB_DMZ wurde dem Angreifer zugeordnet).
"""
from __future__ import annotations

from collections import defaultdict

from app.services.log_watch.definition import Definition
from app.services.log_watch.hosts import Hostverzeichnis

#: Bedrohungsarten (pan_log_subtype), die einzeln gelesen werden. NICHT über gim_event_type:
#: „palo_threat“ steht an ALLEN Palo-Alto-Meldungen, auch am Verkehr (gemessen 06.11.: die
#: Abfrage „palo_threat ohne informational“ traf 188 000 je 5 min statt ~600 — das Fenster
#: war bei 10 000 abgeschnitten, seltene Sinkholes hätten fehlen können).
BEDROHUNG_ARTEN = ("spyware", "virus", "vulnerability", "wildfire", "wildfire-virus", "file",
                   "scan", "flood", "data")
BEDROHUNG_QUERY = ("pan_log_subtype:(" + " OR ".join(f'"{a}"' for a in BEDROHUNG_ARTEN)
                   + ") AND NOT threat_severity:informational")
#: Felder einer Bedrohung, die das Raster braucht (der Rest kommt beim Speichern per ID)
BEDROHUNG_FELDER = ["source", "pan_log_subtype", "action", "threat_id", "threat_severity",
                    "threat_direction", "source_ip", "destination_ip", "source_domain",
                    "destination_domain", "source_zone", "destination_zone",
                    "destination_geo_country", "rule_name", "misc", "timestamp"]
#: Verkehr je Fenster: Zählung nach diesen Feldgruppen (Graylog-Pivot)
ZAEHLUNGEN = {
    "aktion": ["pan_log_subtype", "action"],
    "zonen": ["source_zone", "destination_zone", "action"],
    "zielland_abgelehnt": ["destination_geo_country"],
}
ZAEHL_QUERY = {"aktion": "_exists_:pan_log_subtype",
               "zonen": "_exists_:pan_log_subtype AND action:(deny OR drop)",
               "zielland_abgelehnt": "_exists_:pan_log_subtype AND action:(deny OR drop)"}


def _betroffen(m: dict, externe_zonen: set[str]) -> tuple[str, str, str]:
    """(Name oder IP des betroffenen internen Rechners, seine IP, IP der Gegenstelle)."""
    quelle = ((m.get("source_domain") or m.get("source_ip") or ""), m.get("source_ip") or "")
    ziel = ((m.get("destination_domain") or m.get("destination_ip") or ""), m.get("destination_ip") or "")
    if (m.get("source_zone") or "") in externe_zonen:
        return ziel[0], ziel[1], quelle[1]
    return quelle[0], quelle[1], ziel[1]


def gruppen(msgs: list[dict], externe_zonen: set[str]) -> list[dict]:
    """Bedrohungsmeldungen → Gruppen je (Bedrohung, betroffener Host, Aktion)."""
    g: dict[tuple, dict] = {}
    for m in msgs:
        name, ip, gegen = _betroffen(m, externe_zonen)
        key = (m.get("threat_id") or m.get("misc") or "?", name or ip, m.get("action") or "?")
        x = g.get(key)
        if x is None:
            x = g[key] = {"bedrohung": key[0], "host": name, "ip": ip, "gegenstelle": gegen,
                          "aktion": key[2],
                          "stufe": m.get("threat_severity") or "", "art": m.get("pan_log_subtype") or "",
                          "firewall": (m.get("source") or "").split(".")[0],
                          "zone": m.get("source_zone") or "", "zielzone": m.get("destination_zone") or "",
                          "zielland": m.get("destination_geo_country") or "",
                          "regel_fw": m.get("rule_name") or "", "domain": m.get("misc") or "",
                          "anzahl": 0, "belege": []}
        x["anzahl"] += 1
        if m.get("_id") and len(x["belege"]) < 5:
            x["belege"].append((m["_id"], m.get("timestamp") or ""))
    return list(g.values())


def bewerte_firewall(gruppen_: list[dict], verz: Hostverzeichnis, d: Definition) -> list:
    """Gruppen → Treffer. Bedrohung = Stufe in firewall_bedrohung_stufen ODER Aktion in
    firewall_dringend_aktionen (Sinkhole); dringend bei Sinkhole oder hoher Stufe."""
    from app.services.log_watch.raster import _host_tags, _sig, _urteil

    out = []
    stufen = set(d.p("firewall_bedrohung_stufen"))
    dringend_a = set(d.p("firewall_dringend_aktionen"))
    dringend_s = set(d.p("firewall_dringend_stufen"))
    for x in gruppen_:
        signale: set[str] = set()
        gruende = [f"Firewall {x['firewall']}: {x['art']} „{x['domain'] or x['bedrohung']}“, "
                   f"Aktion {x['aktion']}, Stufe {x['stufe'] or '?'}"]
        if x["stufe"] in stufen or x["aktion"] in dringend_a:
            signale.add("bedrohung")
        if x["aktion"] in dringend_a or x["stufe"] in dringend_s:
            signale.add("dringend")
            if x["aktion"] in dringend_a:
                gruende.append("Sinkhole: ein interner Rechner fragt eine Schadsoftware-Domain an")
        if not signale and x["aktion"] in ("drop", "deny", "drop-packet", "reset-both"):
            gruende.append("von der Firewall abgewehrt, niedrige Stufe")
        kanon, e, htags = _host_tags(x["host"] or x["ip"], verz)
        tags = (["quelle:firewall", f"firewall:{x['firewall']}", f"bedrohung:{x['bedrohung']}",
                 f"aktion:{x['aktion']}", f"schwere:{x['stufe']}", f"zone:{x['zone']}",
                 f"zielzone:{x['zielzone']}", f"zielland:{x['zielland']}"]
                + ([f"ip:{x['ip']}"] if x["ip"] else [])
                + ([f"gegenstelle:{x['gegenstelle']}"] if x.get("gegenstelle") else [])
                + htags + _sig(signale))
        out.append(_urteil("firewall", f"bedrohung:{x['bedrohung']}", kanon, e is not None, tags, {},
                           gruende, x["anzahl"], x["domain"] or x["bedrohung"], d, x["belege"]))
    return out


def zaehlung_aus_pivot(rows: list[dict], felder: list[str], limit: int = 50) -> list[dict]:
    """Pivot-Zeilen → [{feld: wert, …, "anzahl": n}], häufigste zuerst."""
    out = []
    for row in rows:
        if row.get("source") != "leaf" or len(row.get("key") or []) != len(felder):
            continue
        n = next((v.get("value") for v in row.get("values") or []
                  if isinstance(v.get("value"), (int, float))), None)
        if n is not None:
            out.append({**dict(zip(felder, row["key"])), "anzahl": int(n)})
    return sorted(out, key=lambda x: -x["anzahl"])[:limit]


def _pivot(felder: list[str], limit: int) -> dict:
    return {"id": "p", "type": "pivot",
            "row_groups": [{"type": "values", "fields": felder, "limit": limit}],
            "column_groups": [], "series": [{"type": "count", "id": "count()"}],
            "rollup": True, "sort": [], "streams": []}


async def lies_fenster(gl, von, bis) -> tuple[list[dict], dict, bool]:
    """(Bedrohungsmeldungen, Zählungen je Gruppe, vollständig?) eines Fensters."""
    from app.services.graylog_occurrences import iso

    msgs, total = await gl.search_window(BEDROHUNG_QUERY, iso(von), iso(bis), limit=10_000,
                                         fields=BEDROHUNG_FELDER)
    zaehlung: dict[str, list] = {}
    gesamt = 0
    zeit = {"type": "absolute", "from": iso(von), "to": iso(bis)}
    async with gl._client(timeout=120.0) as client:
        for name, felder in ZAEHLUNGEN.items():
            r = await client.post(f"{gl.base_url}/api/views/search/sync", headers=gl._headers(),
                                  json={"queries": [{"id": "q", "timerange": zeit,
                                        "query": {"type": "elasticsearch", "query_string": ZAEHL_QUERY[name]},
                                        "search_types": [_pivot(felder, 50)]}]})
            r.raise_for_status()
            st = (r.json().get("results", {}).get("q", {}).get("search_types", {}) or {}).get("p", {})
            zaehlung[name] = zaehlung_aus_pivot(st.get("rows", []), felder)
            if name == "aktion":
                gesamt = int(st.get("total") or 0)
    zaehlung["gesamt"] = gesamt
    return msgs, zaehlung, total <= len(msgs)


def zusammen(zeilen: list[dict]) -> dict[str, int]:
    """Summe je Aktion aus der Zählung „aktion“ (für Cockpit und Grundlast)."""
    s: dict[str, int] = defaultdict(int)
    for z in zeilen:
        s[z.get("action") or "?"] += z["anzahl"]
    return dict(s)
