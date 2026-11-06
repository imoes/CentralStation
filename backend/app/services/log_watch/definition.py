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


KRITERIEN_V1: tuple[Kriterium, ...] = (
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
              "Sicherheitsereignis ab Wazuh-Level 7 (MITRE-Taktiken eingeschlossen).",
              "Wazuh rule.level ≥ wazuh_level_ab"),
    Kriterium("R7", RELEVANT, "Zusammentreffen",
              "Derselbe Host ist zugleich in zwei Quellen auffällig — gemeinsames Auftreten, "
              "keine belegte Ursache.", "quellenübergreifend, 15 min"),
    Kriterium("R8", RELEVANT, "Ablaufende Frist", "Zertifikat läuft ab, Sicherung fehlgeschlagen.",
              "Themen cert, backup; CheckMK-Dienste Certificate/Backup ≠ OK"),
    Kriterium("N0", NICHT_RELEVANT, "Kein Kriterium erfüllt",
              "Keines der Relevanz-Kriterien trifft zu.", "Standardfall"),
    Kriterium("N1", NICHT_RELEVANT, "Routine", "Bekanntes Muster im üblichen Umfang.",
              "Graylog-Muster ohne Fehler-Cluster-Ereignis"),
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

PARAMETER_V1 = {
    "relevante_kritikalitaeten": ["prod", "staging", "critical"],
    #: CRIT hier ist dringend (3), sonst auffällig (2)
    "dringende_kritikalitaeten": ["prod", "critical"],
    "nicht_produktion": ["test", "dev"],
    "wazuh_level_ab": 7,
    "wazuh_level_dringend": 12,
    "rauschen_regeln": ["5501", "5502"],              # PAM: session opened / closed
    "rauschen_regelgruppen": ["web_scan"],
    "selbst_dienste": ["Check_MK"],
}

def als_daten() -> tuple[list[dict], dict]:
    return [asdict(k) for k in KRITERIEN_V1], dict(PARAMETER_V1)


@dataclass
class Definition:
    version: int
    kriterien: list[dict]
    parameter: dict
    #: Regelwerk (regelwerk.Regel); None → Standardregeln aus den Parametern
    regeln: list | None = None

    def p(self, name: str):
        return self.parameter.get(name, PARAMETER_V1.get(name))

    def __post_init__(self):
        from app.services.log_watch.regelwerk import aus_daten, standard_regeln
        if self.regeln is None:
            self.regeln = standard_regeln(self.p)
        elif self.regeln and isinstance(self.regeln[0], dict):
            self.regeln = aus_daten(self.regeln)


STANDARD = Definition(1, *als_daten())


async def aktuelle(db) -> Definition:
    """Neueste Version aus der DB; gibt es keine, wird Version 1 angelegt."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.log_watch import RelevanzDefinition

    row = (await db.execute(select(RelevanzDefinition)
                            .order_by(RelevanzDefinition.version.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        kriterien, parameter = als_daten()
        row = RelevanzDefinition(version=1, kriterien=kriterien, parameter=parameter,
                                 begruendung="Festgelegt mit dem Nutzer am 06.11.2026",
                                 erstellt_von="system", erstellt_at=datetime.now(timezone.utc))
        db.add(row)
        await db.commit()
    return Definition(row.version, row.kriterien, row.parameter, row.regeln)


def pruefe(kriterien: list[dict], parameter: dict) -> list[str]:
    """Fehler einer neuen Version — leer heißt gültig.

    Die Kennungen sind an Code gebunden (Raster, Vorrang): jede Version muss alle Kennungen
    der Version 1 behalten; Texte und Parameter dürfen sich ändern.
    """
    fehler = []
    ids = [k.get("id") for k in kriterien]
    if len(ids) != len(set(ids)):
        fehler.append("Kennungen doppelt")
    fehlend = sorted({k.id for k in KRITERIEN_V1} - set(ids))
    if fehlend:
        fehler.append("Kennungen fehlen: " + ", ".join(fehlend))
    for k in kriterien:
        if k.get("art") not in (RELEVANT, NICHT_RELEVANT):
            fehler.append(f"{k.get('id')}: art muss relevant oder nicht_relevant sein")
    unbekannt = sorted(set(parameter) - set(PARAMETER_V1))
    if unbekannt:
        fehler.append("unbekannte Parameter: " + ", ".join(unbekannt))
    return fehler
