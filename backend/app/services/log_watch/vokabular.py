"""Festes Startvokabular der Themen-Hashtags (`thema:<id>`).

Die KI vergibt Themen nur aus dieser Liste; neue Themen schlägt sie vor, ein Admin
bestätigt (Plan „Vokabular und Raster“). Jedes Muster matcht mit Wortgrenzen — ein
Teilstring wie „cue“ in „rescue“ ist kein Treffer.

`mindest` = Mindestrelevanz einer Nachricht mit diesem Thema (Schäden an Speicher,
Kernel oder Prozess sind nie Routine).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Thema:
    id: str
    text: str
    muster: re.Pattern
    mindest: int = 0


def _t(id_: str, text: str, muster: str, mindest: int = 0) -> Thema:
    return Thema(id_, text, re.compile(muster, re.IGNORECASE), mindest)


THEMEN: tuple[Thema, ...] = (
    _t("oom", "Speicher erschöpft",
       r"\bout of memory\b|\boom[-_ ]?kill|\bOutOfMemoryError\b|\bcannot allocate memory\b", 2),
    _t("disk-full", "Datenträger voll",
       r"\bno space left on device\b|\bENOSPC\b|\bdisk (is )?full\b|\bfile ?system (is )?full\b", 2),
    _t("fs-readonly", "Dateisystem schreibgeschützt",
       r"\bread-only file ?system\b|\bremounting filesystem read-only\b", 2),
    _t("kernel-io", "E/A-Fehler",
       r"\bI/O error\b|\bblk_update_request\b|\bEXT4-fs error\b|\bXFS \(\S+\): .*error\b", 2),
    _t("absturz", "Prozess abgestürzt",
       r"\bsegfault\b|\bsegmentation fault\b|\bcore dumped\b|\bkernel panic\b"
       r"|\bkilled by signal\b|\bunexpectedly (exited|terminated)\b", 2),
    _t("crashloop", "Neustart-Schleife",
       r"\bCrashLoopBackOff\b|\bback-off restarting\b|\brestart(ing)? loop\b"),
    _t("cert", "Zertifikat",
       r"\bcertificate (has )?expired\b|\bcertificate (verify|verification) failed\b"
       r"|\bx509:|\bcert(ificate)? (is )?not yet valid\b"),
    _t("tls", "TLS-Handshake",
       r"\b(SSL|TLS)[ _-]?(handshake|alert)\b|\bhandshake failure\b|\bSSL routines\b"),
    _t("dns", "Namensauflösung",
       r"\bNXDOMAIN\b|\bSERVFAIL\b|\bname or service not known\b"
       r"|\btemporary failure in name resolution\b|\bcould not resolve host\b"),
    _t("timeout", "Zeitüberschreitung",
       r"\btimed out\b|\btime-?out\b|\bdeadline exceeded\b"),
    _t("conn-refused", "Verbindung abgelehnt", r"\bconnection refused\b|\bECONNREFUSED\b"),
    _t("conn-reset", "Verbindung abgebrochen",
       r"\bconnection reset\b|\bECONNRESET\b|\bbroken pipe\b"),
    _t("auth-fail", "Anmeldung fehlgeschlagen",
       r"\bauthentication fail(ed|ure)\b|\binvalid (user|password|credentials)\b"
       r"|\blogin failed\b|\b401 unauthorized\b|\bpermission denied \(publickey"),
    _t("zugriff", "Zugriff verweigert",
       r"\bpermission denied\b|\bEACCES\b|\b403 forbidden\b|\baccess denied\b"),
    _t("http-5xx", "Serverfehler (HTTP 5xx)",
       r"\b5\d\d (internal server error|bad gateway|service unavailable|gateway time-?out)\b"
       r"|\bstatus(code)?[=: \"]+5\d\d\b|\" 5\d\d \d"),
    _t("datenbank", "Datenbank",
       r"\btoo many connections\b|\bcould not connect to (server|database)\b|\bSQLSTATE\b"
       r"|\bORA-\d{5}\b|\bconnection pool (is )?exhausted\b|\bdeadlock\b"),
    _t("replikation", "Replikation",
       r"\breplication\b.{0,40}\b(lag|error|broken|stopped|failed)\b"),
    _t("backup", "Sicherung fehlgeschlagen",
       r"\bbackup\b.{0,40}\b(fail(ed|ure)?|error)\b|\brestic\b.{0,40}\berror\b"),
    _t("drosselung", "Drosselung", r"\brate limit(ed)?\b|\btoo many requests\b"),
    _t("warteschlange", "Warteschlange",
       r"\bqueue (is )?full\b|\bmessages? (dropped|discarded)\b|\bbacklog\b"),
)
THEMA_IDS = frozenset(t.id for t in THEMEN)


def themen(text: str) -> list[Thema]:
    """Alle Themen, deren Muster im Text vorkommt — in Vokabular-Reihenfolge."""
    return [t for t in THEMEN if t.muster.search(text or "")]


#: CheckMK: der Service-Name sagt das Thema, die Ausgabe ist oft nur „95 % used“.
#: (Präfix des Service-Namens, Thema-ID)
DIENST_THEMEN: tuple[tuple[str, str], ...] = (
    ("Filesystem", "disk-full"),
    ("Disk IO", "kernel-io"),
    ("HTTPS Certificate", "cert"),
    ("Certificate", "cert"),
    ("Memory", "oom"),
    ("Backup", "backup"),
    ("Restic", "backup"),
    ("MySQL", "datenbank"),
    ("Postgres", "datenbank"),
    ("Oracle", "datenbank"),
    ("DNS", "dns"),
)


def dienst_thema(service: str) -> str | None:
    s = service or ""
    return next((t for p, t in DIENST_THEMEN if s.startswith(p)), None)
