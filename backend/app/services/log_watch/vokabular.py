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
    #: typische Zeile — Dokumentation, Test (muss passen) und Widerspruchsprüfung gegen Stufe 0
    beispiel: str = ""


def _t(id_: str, text: str, muster: str, mindest: int = 0, beispiel: str = "") -> Thema:
    return Thema(id_, text, re.compile(muster, re.IGNORECASE), mindest, beispiel)


THEMEN: tuple[Thema, ...] = (
    _t("oom", "Speicher erschöpft",
       r"\bout of memory\b|\boom[-_ ]?kill|\bOutOfMemoryError\b|\bcannot allocate memory\b", 2, beispiel='kernel: Out of memory: Killed process 4711 (java)'),
    _t("disk-full", "Datenträger voll",
       r"\bno space left on device\b|\bENOSPC\b|\bdisk (is )?full\b|\bfile ?system (is )?full\b", 2, beispiel='write /data/x: no space left on device'),
    _t("fs-readonly", "Dateisystem schreibgeschützt",
       r"\bread-only file ?system\b|\bremounting filesystem read-only\b", 2, beispiel='EXT4-fs (sda1): Remounting filesystem read-only'),
    _t("kernel-io", "E/A-Fehler",
       r"\bI/O error\b|\bblk_update_request\b|\bEXT4-fs error\b|\bXFS \(\S+\): .*error\b", 2, beispiel='blk_update_request: I/O error, dev sda, sector 12345'),
    _t("absturz", "Prozess abgestürzt",
       r"\bsegfault\b|\bsegmentation fault\b|\bcore dumped\b|\bkernel panic\b"
       r"|\bkilled by signal\b|\bunexpectedly (exited|terminated)\b", 2, beispiel='nginx[123]: segfault at 0 ip 00007f'),
    _t("crashloop", "Neustart-Schleife",
       r"\bCrashLoopBackOff\b|\bback-off restarting\b|\brestart(ing)? loop\b", beispiel='Back-off restarting failed container'),
    _t("cert", "Zertifikat",
       r"\bcertificate (has )?expired\b|\bcertificate (verify|verification) failed\b"
       r"|\bx509:|\bcert(ificate)? (is )?not yet valid\b", beispiel='x509: certificate has expired or is not yet valid'),
    _t("tls", "TLS-Handshake",
       r"\b(SSL|TLS)[ _-]?(handshake|alert)\b|\bhandshake failure\b|\bSSL routines\b", beispiel='SSL routines:ssl3_read_bytes:sslv3 alert handshake failure'),
    _t("dns", "Namensauflösung",
       r"\bNXDOMAIN\b|\bSERVFAIL\b|\bname or service not known\b"
       r"|\btemporary failure in name resolution\b|\bcould not resolve host\b", beispiel='lookup db01: NXDOMAIN'),
    _t("timeout", "Zeitüberschreitung",
       r"\btimed out\b|\btime-?out\b|\bdeadline exceeded\b", beispiel='upstream timed out (110) while reading response header'),
    _t("conn-refused", "Verbindung abgelehnt", r"\bconnection refused\b|\bECONNREFUSED\b", beispiel='dial tcp 10.0.0.5:5432: connect: connection refused'),
    _t("conn-reset", "Verbindung abgebrochen",
       r"\bconnection reset\b|\bECONNRESET\b|\bbroken pipe\b", beispiel='read: connection reset by peer'),
    _t("auth-fail", "Anmeldung fehlgeschlagen",
       r"\bauthentication fail(ed|ure)\b|\binvalid (user|password|credentials)\b"
       r"|\blogin failed\b|\b401 unauthorized\b|\bpermission denied \(publickey", beispiel='pam_unix(sshd:auth): authentication failure; user=root'),
    _t("zugriff", "Zugriff verweigert",
       r"\bpermission denied\b|\bEACCES\b|\b403 forbidden\b|\baccess denied\b", beispiel='open /etc/app.conf: permission denied'),
    _t("http-5xx", "Serverfehler (HTTP 5xx)",
       r"\b5\d\d (internal server error|bad gateway|service unavailable|gateway time-?out)\b"
       r"|\bstatus(code)?[=: \"]+5\d\d\b|\" 5\d\d \d", beispiel='"GET /api HTTP/1.1" 502 173'),
    _t("datenbank", "Datenbank",
       r"\btoo many connections\b|\bcould not connect to (server|database)\b|\bSQLSTATE\b"
       r"|\bORA-\d{5}\b|\bconnection pool (is )?exhausted\b|\bdeadlock\b", beispiel='FATAL: sorry, too many connections'),
    _t("replikation", "Replikation",
       r"\breplication\b.{0,40}\b(lag|error|broken|stopped|failed)\b", beispiel='replication lag 120s on replica db02'),
    _t("backup", "Sicherung fehlgeschlagen",
       r"\bbackup\b.{0,40}\b(fail(ed|ure)?|error)\b|\brestic\b.{0,40}\berror\b", beispiel='backup job nightly failed: exit code 1'),
    _t("drosselung", "Drosselung", r"\brate limit(ed)?\b|\btoo many requests\b", beispiel='429 Too Many Requests: rate limit exceeded'),
    _t("warteschlange", "Warteschlange",
       r"\bqueue (is )?full\b|\bmessages? (dropped|discarded)\b|\bbacklog\b", beispiel='queue is full, messages dropped'),
)
#: Switches (nsa/nss/nsc) — eigene Wortwahl der Hersteller (HPE/Aruba, Cisco, Juniper)
THEMEN_SWITCH: tuple[Thema, ...] = (
    _t("link-down", "Verbindung unterbrochen",
       r"\blink (is )?down\b|\bline protocol on interface \S+, changed state to down\b"
       r"|\bport \S+ (is )?(now )?(off-line|down)\b|\binterface \S+ (is )?down\b"
       r"|\binterface \S+, changed state to down\b"
       # Juniper EX (gemessen 06.11.): „ex_bcm_linkscan_handler: Link 12 DOWN“, SNMP-Trap
       r"|\bLink \d+ DOWN\b|SNMP_TRAP_LINK_DOWN|\bifOperStatus down\b"),
    _t("err-disable", "Port abgeschaltet",
       r"\berr-?disable|\bdisabled (by|due to) (bpdu|loop|port-security|storm)"),
    _t("stp", "Spanning-Tree-Änderung",
       r"\btopology change\b|\bTCN\b|\broot (bridge|port) (changed|is)\b|\bspanning[- ]tree\b"),
    _t("schleife", "Netzwerkschleife", r"\bloop (detected|protect)|\bbroadcast storm\b"),
    _t("crc", "Übertragungsfehler", r"\bCRC\b|\bFCS errors?\b|\binput errors\b"),
    _t("poe", "PoE-Fehler", r"\bPoE\b.{0,40}\b(fault|overload|denied|failed)\b"),
    _t("hardware", "Hardwarefehler",
       r"\b(power supply|PSU|fan|temperature)\b.{0,40}\b(fail(ed|ure)?|fault|critical|too high|over)\b", 2),
)
THEMA_IDS = frozenset(t.id for t in THEMEN + THEMEN_SWITCH)


def themen_switch(text: str) -> list[Thema]:
    """Allgemeine Themen und Switch-Themen — in Vokabular-Reihenfolge."""
    return themen(text) + [t for t in THEMEN_SWITCH if t.muster.search(text or "")]


def themen(text: str) -> list[Thema]:
    """Alle Themen, deren Muster im Text vorkommt — in Vokabular-Reihenfolge."""
    return [t for t in THEMEN if t.muster.search(text or "")]


def themen_ids(text: str) -> tuple[str, ...]:
    """Themen-IDs einer Zeile (für die Gruppen der Fehler-Cluster-Zählung)."""
    first = (text or "").split("\n", 1)[0]
    return tuple(t.id for t in THEMEN if t.muster.search(first))


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
