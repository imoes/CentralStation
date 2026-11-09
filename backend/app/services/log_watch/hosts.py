"""Hostnamen über alle Quellen auf EINEN Namen bringen — wie Namen im Recherche-Tool.

Dieselbe Maschine heißt je Quelle anders (gemessen 06.11.: `WEC01`/`wec01`,
`aim012`/`aim12`, Kurzname vs. FQDN mit/ohne Schlusspunkt). `norm()` bildet jede
Schreibweise auf einen Vergleichsschlüssel ab; kanonisch ist der Name aus der
CheckMK-Hostliste. Ein Host, den CheckMK nicht kennt, behält seinen normierten Namen und
ist als unbekannt benannt (`hostliste:unbekannt`) — er verschwindet nicht.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_FUEHRENDE_NULL = re.compile(r"(?<=\D)0+(?=\d)")
#: Docker-GELF setzt als Quelle die Container-ID (10–12 Hex-Zeichen) — kein Host.
_CONTAINER_ID = re.compile(r"^[0-9a-f]{10,12}$")
#: Hostliste höchstens so lange wiederverwenden (Sekunden).
CACHE_S = 3600


def _ist_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def norm(name: str) -> str:
    """Vergleichsschlüssel: klein, ohne Domain, ohne führende Nullen in Zahlen.

    `AIM012.example.com.` → `aim12`; IP-Adressen bleiben, wie sie sind.
    """
    s = (name or "").strip().strip("[]").rstrip(".").lower()
    if not s or _ist_ip(s):
        return s
    kurz = s.split(".", 1)[0]
    return _FUEHRENDE_NULL.sub("", kurz)


@dataclass
class HostEintrag:
    name: str                        # kanonisch: wie in CheckMK
    ort: str = ""
    kritikalitaet: str = ""
    hostgruppen: list[str] = field(default_factory=list)
    #: Netzbereich aus dem CheckMK-Ordner: dmz | intern ("" = kein Ordner bekannt)
    netz: str = ""
    #: das Ordnersegment mit „dmz“ (z. B. dmz-ii, web-dmz) — der genaue Bereich
    netzbereich: str = ""
    #: IP-Adresse aus CheckMK (Konfiguration `ipaddress`, sonst Livestatus `address`) — "" = keine bekannt
    adresse: str = ""


#: Warum ein Name keinem CheckMK-Host zugeordnet ist — benannt, nicht geraten.
UNBEKANNT = "unbekannt"            # kommt in CheckMK nicht vor
MEHRDEUTIG = "mehrdeutig"          # passt auf mehrere CheckMK-Hosts
CONTAINER_ID = "container-id"      # Docker-Container-ID statt Hostname


@dataclass
class Hostverzeichnis:
    eintraege: dict[str, HostEintrag] = field(default_factory=dict)   # norm → Eintrag
    #: Vergleichsschlüssel, die auf mehrere CheckMK-Hosts passen — dort wird nicht geraten.
    mehrdeutig: set[str] = field(default_factory=set)
    #: voller Name (klein, ohne Schlusspunkt) und IP-Adresse → Eintrag; schlägt `norm` vor
    exakt: dict[str, HostEintrag] = field(default_factory=dict)
    #: Domains der CheckMK-Hosts ohne Punkte („REDACTED-CREDENTIAL“) — manche Quellen kleben sie an
    #: den Kurznamen („cue0104REDACTED-CREDENTIAL“).
    domains_ohne_punkt: tuple[str, ...] = ()

    @classmethod
    def aus_checkmk(cls, hosts: list[dict]) -> "Hostverzeichnis":
        v = cls()
        domains: set[str] = set()
        for h in hosts:
            name = h.get("hostname") or ""
            if not name:
                continue
            md = h.get("metadata") or {}
            e = HostEintrag(name=name, ort=md.get("location") or "",
                            kritikalitaet=md.get("criticality") or "",
                            hostgruppen=list(md.get("hostgroups") or []),
                            netz=md.get("netz") or "", netzbereich=md.get("netzbereich") or "",
                            adresse=str(md.get("ipaddress") or "").strip())
            v.exakt[name.lower().rstrip(".")] = e
            if md.get("ipaddress"):
                v.exakt[str(md["ipaddress"]).strip()] = e
            if "." in name and not _ist_ip(name):
                domains.add(name.lower().split(".", 1)[1].replace(".", ""))
            k = norm(name)
            if k in v.eintraege and v.eintraege[k].name != name:
                v.mehrdeutig.add(k)
                continue
            v.eintraege[k] = e
        for k in v.mehrdeutig:
            v.eintraege.pop(k, None)
        v.domains_ohne_punkt = tuple(sorted(domains, key=len, reverse=True))
        return v

    def als_liste(self) -> list[dict]:
        """Jeder CheckMK-Host genau einmal, nach Name sortiert — für lw_hostverzeichnis. Rein.

        Aus `exakt` (Name und IP zeigen auf denselben Eintrag), nicht aus `eintraege`: dort fehlen die Hosts mit
        mehrdeutigem Kurznamen — die gibt es aber, nur ihr Kurzname löst nicht auf."""
        mehr = {e.name for k in self.mehrdeutig for e in self.exakt.values() if norm(e.name) == k}
        return [{"name": e.name, "ort": e.ort, "kritikalitaet": e.kritikalitaet, "hostgruppen": list(e.hostgruppen),
                 "netz": e.netz, "netzbereich": e.netzbereich, "adresse": e.adresse,
                 "kurzname_mehrdeutig": e.name in mehr}
                for e in sorted({id(e): e for e in self.exakt.values()}.values(), key=lambda e: e.name.lower())]

    def kanon(self, name: str) -> tuple[str, HostEintrag | None]:
        """(kanonischer Name, Eintrag) — ohne Eintrag: normierter Name, Host nicht zugeordnet."""
        e, k, _ = self.zuordnen(name)
        return (e.name, e) if e else (k, None)

    def zuordnen(self, name: str) -> tuple[HostEintrag | None, str, str]:
        """(Eintrag, Vergleichsschlüssel, Grund wenn kein Eintrag).

        Reihenfolge: exakter Name/IP → Kurzname → Kurzname ohne angeklebte Domain.
        """
        roh = (name or "").strip().strip("[]").rstrip(".").lower()
        if roh in self.exakt:
            return self.exakt[roh], norm(roh), ""
        k = norm(roh)
        if _CONTAINER_ID.match(k):
            return None, k, CONTAINER_ID
        kandidaten = [k] + [k[:-len(d)] for d in self.domains_ohne_punkt
                            if k.endswith(d) and len(k) > len(d)]
        for kk in kandidaten:
            kk = _FUEHRENDE_NULL.sub("", kk)
            if kk in self.eintraege:
                return self.eintraege[kk], kk, ""
        if any(_FUEHRENDE_NULL.sub("", kk) in self.mehrdeutig for kk in kandidaten):
            return None, k, MEHRDEUTIG
        return None, k, UNBEKANNT


_cache: tuple[float, Hostverzeichnis] | None = None


def alter_s() -> float | None:
    """Sekunden seit dem Laden des zwischengespeicherten Verzeichnisses (None = noch nicht geladen)."""
    return round(time.monotonic() - _cache[0], 1) if _cache else None


async def verzeichnis(db) -> Hostverzeichnis:
    """Hostverzeichnis aus CheckMK, eine Stunde zwischengespeichert. Ohne CheckMK: leer."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_S:
        return _cache[1]
    from sqlalchemy import select

    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig
    from app.services.connectors.checkmk import CheckMKConnector

    c = (await db.execute(select(ConnectorConfig).where(
        ConnectorConfig.type == "checkmk", ConnectorConfig.enabled.is_(True)))).scalars().first()
    v = Hostverzeichnis()
    if c is not None:
        k = CheckMKConnector(base_url=c.base_url, credentials=decrypt_credentials(c.encrypted_credentials))
        hosts = await k.get_all_hosts()
        # Die aufgelöste Adresse steht in Livestatus (`address`), nicht in der Konfiguration —
        # dort ist `ipaddress` meist leer (gemessen 06.11.: IP-Quellen blieben unzugeordnet).
        try:
            r = await k._request("GET", "/domain-types/host/collections/all",
                                 params={"columns": ["name", "address"]})
            r.raise_for_status()
            adressen = {x["extensions"].get("name"): x["extensions"].get("address")
                        for x in r.json().get("value") or []}
            for h in hosts:
                md = h.setdefault("metadata", {})
                if not md.get("ipaddress") and adressen.get(h.get("hostname")):
                    md["ipaddress"] = adressen[h["hostname"]]
        except Exception as e:  # noqa: BLE001 — ohne Adressen: nur IP-Zuordnung fehlt
            log.warning("Hostverzeichnis: Adressen nicht geladen: %s", e)
        v = Hostverzeichnis.aus_checkmk(hosts)
    if not v.eintraege:
        log.warning("Hostverzeichnis leer — alle Hosts gelten als unbekannt")
    _cache = (time.monotonic(), v)
    return v
