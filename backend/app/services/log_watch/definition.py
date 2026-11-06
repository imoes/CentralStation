"""Was in unserem Netzwerk relevant ist — und was nicht. Die Regel (Intension) hinter jedem
Urteil (Extension): jedes Urteil nennt die Kriterien, auf die es sich stützt.

Festgelegt mit dem Nutzer am 06.11.2026. Versioniert in `relevanz_definition`; ein Admin
ändert (neue Version mit Begründung), die KI darf Kriterien vorschlagen. Version 1 steht
hier und wird beim ersten Lesen in die DB übernommen.

Die maschinelle Form ist das Regelwerk (regelwerk.py, Firewall-Semantik): geordnete Regeln,
die erste passende entscheidet. Die Standard-Reihenfolge ist der vereinbarte Vorrang
  N6 stumm > N2 behandelt > N4 Rauschen > R5/R6 vor N3/N5 > N3/N5 vor übrigen R > R vor N1
N4 steht über R6, weil Rauschen per Definition Sicherheitsereignisse ab Level 7 betrifft
(Webscanner) — stünde R6 darüber, wäre N4 wirkungslos (Logikprüfung 06.11.).
Default-Policy N0 „kein Kriterium erfüllt“ macht die Einteilung erschöpfend.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

RELEVANT, NICHT_RELEVANT = "relevant", "nicht_relevant"


@dataclass(frozen=True)
class Kriterium:
    id: str
    art: str            # relevant | nicht_relevant
    titel: str
    beschreibung: str
    messung: str        # woran das Raster es erkennt — oder „ki“, wenn nur die KI urteilen kann


KRITERIEN: tuple[Kriterium, ...] = (
    Kriterium("R1", RELEVANT, "Störung in Produktion",
              "Ein Host oder Dienst mit Kritikalität prod, staging oder critical ist gestört.",
              "CheckMK CRIT; Fehlerausbruch auf einem solchen Host"),
    Kriterium("R2", RELEVANT, "Neues Muster", "Ein Fehlermuster tritt zum ersten Mal auf.",
              "Fehler-Cluster-Regel „neu“"),
    Kriterium("R3", RELEVANT, "Ausbreitung", "Derselbe Fehler auf mehreren Hosts zugleich.",
              "Fehler-Cluster-Regel „Ausbreitung“"),
    Kriterium("R4", RELEVANT, "Anstieg", "Ein bekanntes Muster deutlich über seinem Üblichen.",
              "Fehler-Cluster-Regel „Anstieg“ (Poisson gegen die Grundlast)"),
    Kriterium("R5", RELEVANT, "Schaden an Ressourcen",
              "Speicher erschöpft, Platte voll, Dateisystem schreibgeschützt, E/A-Fehler, Absturz.",
              "Themen oom, disk-full, fs-readonly, kernel-io, absturz; Kernel-Signaturen; "
              "CheckMK CRIT auf Filesystem/Memory/Disk IO"),
    Kriterium("R6", RELEVANT, "Sicherheit",
              "Sicherheitsereignis: Wazuh ab Level 7 (MITRE-Taktiken eingeschlossen); "
              "Firewall-Bedrohung mit Sinkhole oder Stufe ab medium.",
              "Wazuh rule.level ≥ wazuh_level_ab; Firewall: action in firewall_dringend_aktionen "
              "oder threat_severity in firewall_bedrohung_stufen"),
    Kriterium("R7", RELEVANT, "Zusammentreffen",
              "Derselbe Host ist zugleich in zwei Quellen auffällig — gemeinsames Auftreten, "
              "keine belegte Ursache.", "quellenübergreifend, 15 min"),
    Kriterium("R8", RELEVANT, "Ablaufende Frist", "Zertifikat läuft ab, Sicherung fehlgeschlagen.",
              "Themen cert, backup; CheckMK-Dienste Certificate/Backup ≠ OK"),
    Kriterium("N0", NICHT_RELEVANT, "Kein Kriterium erfüllt",
              "Keines der Relevanz-Kriterien trifft zu.", "Standardfall"),
    Kriterium("N1", NICHT_RELEVANT, "Routine", "Bekanntes Muster im üblichen Umfang.",
              "Graylog-Muster ohne Fehler-Cluster-Ereignis; Switch-Muster ohne Signal"),
    Kriterium("N2", NICHT_RELEVANT, "Bekannt und behandelt",
              "Quittiert, in Downtime, offenes Ticket dazu oder bereits als KI-Befund gemeldet "
              "(solange kein neues Relevanz-Kriterium hinzukommt).",
              "CheckMK acknowledged/Downtime; Ticket und Befund: noch nicht angebunden"),
    Kriterium("N3", NICHT_RELEVANT, "Nicht-Produktion", "Host mit Kritikalität test oder dev.",
              "CheckMK-Kritikalität in nicht_produktion"),
    Kriterium("N4", NICHT_RELEVANT, "Rauschen",
              "Webscanner-Zugriffe, PAM-Sitzung geöffnet/geschlossen, einzelne Anmeldefehler "
              "ohne Muster.", "Wazuh-Regeln/-Regelgruppen in rauschen_*"),
    Kriterium("N5", NICHT_RELEVANT, "Selbst erzeugt",
              "Vom Monitoring selbst erzeugt (Check_MK-Checks, Agent-Updates).",
              "CheckMK-Dienste mit Präfix in selbst_dienste"),
    Kriterium("N6", NICHT_RELEVANT, "Stumm geschaltet", "Ein Admin hat das Muster stumm geschaltet.",
              "LogTemplate.muted"),
)

PARAMETER = {
    "relevante_kritikalitaeten": ["prod", "staging", "critical"],
    #: CRIT hier ist dringend (3), sonst auffällig (2)
    "dringende_kritikalitaeten": ["prod", "critical"],
    "nicht_produktion": ["test", "dev"],
    "wazuh_level_ab": 7,
    "wazuh_level_dringend": 12,
    "rauschen_regeln": ["5501", "5502"],              # PAM: session opened / closed
    "rauschen_regelgruppen": ["web_scan"],
    "selbst_dienste": ["Check_MK"],
    #: Firewall (Palo Alto): Bedrohung = Stufe hier ODER Aktion in firewall_dringend_aktionen
    "firewall_bedrohung_stufen": ["medium", "high", "critical"],
    #: Sinkhole: ein interner Rechner fragt eine Schadsoftware-Domain an — dringend
    "firewall_dringend_aktionen": ["sinkhole"],
    "firewall_dringend_stufen": ["high", "critical"],
    #: Zonen außerhalb unseres Netzes — kommt eine Verbindung von dort, ist das Ziel betroffen
    "firewall_externe_zonen": ["UNTRUST"],
}

#: Version der Definition, die dieser Code als Standard mitbringt, und warum es sie gibt.
CODE_VERSION = 4
AENDERUNGEN = {
    1: "Festgelegt mit dem Nutzer am 06.11.2026",
    2: "R6 umfasst Firewall-Bedrohungen (Sinkhole, Stufe ab medium) — Nutzer 06.11.2026: "
       "Firewall- und Switch-Logs werden mit analysiert",
    3: "Firewall: betroffen ist die Seite in einer internen Zone (Parameter firewall_externe_zonen) — "
       "Logikprüfung 06.11.: ein Angriff von außen wurde dem Angreifer statt dem Ziel zugeordnet",
    4: "Switch-Logs werden analysiert (eigener Musterlerner): bekanntes Switch-Muster ohne Signal ist "
       "Routine (N1), Hardwarefehler sind Schaden (R5), Syslog-Level ≤ 2 ist ein kritischer Zustand",
}

def als_daten() -> tuple[list[dict], dict]:
    return [asdict(k) for k in KRITERIEN], dict(PARAMETER)


@dataclass
class Definition:
    version: int
    kriterien: list[dict]
    parameter: dict
    #: Regelwerk (regelwerk.Regel); None → Standardregeln aus den Parametern
    regeln: list | None = None
    #: Der Code bringt eine neuere Standard-Version mit, die letzte Version stammt aber von
    #: einem Admin — sie wird nicht überschrieben, der Unterschied ist benannt.
    code_neuer: bool = False

    def p(self, name: str):
        return self.parameter.get(name, PARAMETER.get(name))

    def __post_init__(self):
        from app.services.log_watch.regelwerk import aus_daten, standard_regeln
        if self.regeln is None:
            self.regeln = standard_regeln(self.p)
        elif self.regeln and isinstance(self.regeln[0], dict):
            self.regeln = aus_daten(self.regeln)


STANDARD = Definition(CODE_VERSION, *als_daten())


def uebernahme(version: int | None, von: str | None) -> str:
    """Was mit der letzten DB-Version gegenüber dem Code-Standard geschieht — erschöpfend:
    leer (anlegen) · system_aelter (übernehmen) · admin_aelter (bleibt, benannt) · aktuell."""
    if version is None:
        return "leer"
    if version >= CODE_VERSION:
        return "aktuell"
    return "system_aelter" if von == "system" else "admin_aelter"


async def aktuelle(db) -> Definition:
    """Neueste Version aus der DB.

    Gibt es keine, wird die Code-Version angelegt. Ist die letzte Version älter als der Code
    und stammt sie vom System, wird die Code-Version übernommen (neue Zeile mit Grund). Stammt
    sie von einem Admin, bleibt sie und `code_neuer` sagt, dass der Standard weiter ist.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.log_watch import RelevanzDefinition

    row = (await db.execute(select(RelevanzDefinition)
                            .order_by(RelevanzDefinition.version.desc()).limit(1))).scalar_one_or_none()
    fall = uebernahme(None if row is None else row.version, None if row is None else row.erstellt_von)
    code_neuer = fall == "admin_aelter"
    if fall in ("leer", "system_aelter"):
        kriterien, parameter = als_daten()
        neu = CODE_VERSION if row is None else max(row.version + 1, CODE_VERSION)
        row = RelevanzDefinition(version=neu, kriterien=kriterien, parameter=parameter,
                                 begruendung=AENDERUNGEN[CODE_VERSION], erstellt_von="system",
                                 erstellt_at=datetime.now(timezone.utc))
        db.add(row)
        await db.commit()
    return Definition(row.version, row.kriterien, row.parameter, row.regeln, code_neuer)


def pruefe(kriterien: list[dict], parameter: dict) -> list[str]:
    """Fehler einer neuen Version — leer heißt gültig.

    Die Kennungen sind an Code gebunden (Raster, Vorrang): jede Version muss alle Kennungen
    des Code-Standards behalten; Texte und Parameter dürfen sich ändern.
    """
    fehler = []
    ids = [k.get("id") for k in kriterien]
    if len(ids) != len(set(ids)):
        fehler.append("Kennungen doppelt")
    fehlend = sorted({k.id for k in KRITERIEN} - set(ids))
    if fehlend:
        fehler.append("Kennungen fehlen: " + ", ".join(fehlend))
    for k in kriterien:
        if k.get("art") not in (RELEVANT, NICHT_RELEVANT):
            fehler.append(f"{k.get('id')}: art muss relevant oder nicht_relevant sein")
    unbekannt = sorted(set(parameter) - set(PARAMETER))
    if unbekannt:
        fehler.append("unbekannte Parameter: " + ", ".join(unbekannt))
    return fehler
