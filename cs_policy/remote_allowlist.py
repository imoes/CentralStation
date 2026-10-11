"""Positivliste für freie Lese-Befehle auf Servern — die Grenze der SSH-Schleuse.

Die Schleuse (userenv, root) lässt einen Befehl des Agenten ohne Freigabe nur durch, wenn
check() ihn akzeptiert. Geprüft wird POSITIV: jedes Programm einer Befehlskette muss hier
stehen, mit seinem Unterbefehls-Prüfer. Was nicht dasteht, ist keine Leseoperation im Sinne
der Schleuse — dafür gibt es die Freigabe. (Der Regex-Guard im Container bleibt ein Hinweis
vorab, keine Grenze.)

Grundregeln für die ganze Zeile:
- keine Ersetzungen/Unterprozesse ($, Backtick außerhalb einfacher Anführungszeichen),
  keine Klammern/Gruppen, kein Hintergrund (&), keine Umleitungen außer 2>/dev/null,
  >/dev/null, 2>&1
- Ketten nur mit |, &&, ||, ;
- vorangestellt erlaubt: `sudo -n` und `timeout <n>`
- sensible Pfade (Schlüssel, Passwörter, Umgebungsdateien) sind auch lesend gesperrt:
  die Ausgabe geht an ein Sprachmodell.
"""
from __future__ import annotations

import re
import shlex

from cs_policy.templates import PolicyError

MAX_LEN = 4096
_ALLOWED_REDIRECTS = re.compile(r"(?<!\S)(?:2>/dev/null|>/dev/null|2>&1)(?!\S)")
_OPERATORS = {"|", "||", "&&", ";"}
_SENSITIVE = re.compile(
    r"(?i)(/etc/(g?shadow|sudoers)|id_(rsa|dsa|ecdsa|ed25519)|\.ssh/|authorized_keys|"
    r"\.(key|pem|p12|pfx|kdbx)$|(^|/)\.env(\.|$)|/proc/[^/]+/environ|credentials|secret|"
    r"\.netrc|\.pgpass|\.my\.cnf|vault)")
_STD_DIRS = ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/", "/usr/local/bin/", "/usr/local/sbin/")


# ── Zeilen-Prüfung ────────────────────────────────────────────────────────────

def _scan(cmd: str) -> None:
    """Metazeichen außerhalb einfacher Anführungszeichen ablehnen."""
    state = None                     # None | "'" | '"'
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if state == "'":
            if ch == "'":
                state = None
        elif ch == "\\":
            raise PolicyError("Backslash-Escapes sind nicht erlaubt")
        elif state == '"':
            if ch == '"':
                state = None
            elif ch in "$`":
                raise PolicyError("Ersetzungen ($, `) sind nicht erlaubt")
        else:
            if ch in "'\"":
                state = ch
            elif ch in "$`":
                raise PolicyError("Ersetzungen ($, `) sind nicht erlaubt")
            elif ch in "(){}<>":
                raise PolicyError(f"'{ch}' ist nicht erlaubt (Gruppen, Umleitungen)")
            elif ch == "&" and not cmd.startswith("&&", i) and not cmd.startswith("&&", i - 1):
                raise PolicyError("Hintergrund-Prozesse (&) sind nicht erlaubt")
        i += 1
    if state is not None:
        raise PolicyError("Unvollständige Anführungszeichen")


def _segments(cmd: str) -> list[list[str]]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    segs: list[list[str]] = [[]]
    for tok in lex:
        if tok in _OPERATORS:
            segs.append([])
        elif set(tok) <= set(";&|"):
            raise PolicyError(f"Operator '{tok}' ist nicht erlaubt")
        else:
            segs[-1].append(tok)
    if any(not s for s in segs):
        raise PolicyError("Leerer Befehl in der Kette")
    return segs


def _unwrap(argv: list[str]) -> list[str]:
    """`sudo -n` und `timeout N` vorne abnehmen."""
    while argv:
        if argv[0] == "sudo":
            if len(argv) < 3 or argv[1] != "-n":
                raise PolicyError("sudo nur als 'sudo -n <lesender Befehl>'")
            argv = argv[2:]
        elif argv[0] == "timeout":
            if len(argv) < 3 or not re.fullmatch(r"\d{1,4}[smh]?", argv[1]):
                raise PolicyError("timeout nur als 'timeout <n> <befehl>'")
            argv = argv[2:]
        else:
            return argv
    raise PolicyError("Leerer Befehl")


def _prog(arg0: str) -> str:
    if "/" in arg0:
        if not arg0.startswith(_STD_DIRS):
            raise PolicyError(f"Programm {arg0} liegt nicht in einem Standardverzeichnis")
        return arg0.rsplit("/", 1)[1]
    return arg0


# ── Prüfer je Programm ────────────────────────────────────────────────────────

def _deny_flags(*flags: str):
    def check(args: list[str]) -> None:
        for a in args:
            for f in flags:
                if a == f or (f.startswith("--") and a.startswith(f + "=")) or \
                        (len(f) == 2 and not f.startswith("--") and a.startswith("-")
                         and not a.startswith("--") and f[1] in a[1:]):
                    raise PolicyError(f"Option {f} ist nicht erlaubt")
    return check


def _no_args(args: list[str]) -> None:
    if args:
        raise PolicyError("ohne Argumente erlaubt")


def _sub(allowed: set[str], *, skip_opts: bool = True, extra=None):
    """Erster Nicht-Options-Parameter muss ein erlaubter Unterbefehl sein."""
    def check(args: list[str]) -> None:
        rest = [a for a in args if not a.startswith("-")] if skip_opts else args
        if not rest or rest[0] not in allowed:
            raise PolicyError(f"Unterbefehl nur: {', '.join(sorted(allowed))}")
        if extra:
            extra(args, rest)
    return check


def _docker(args: list[str]) -> None:
    rest = [a for a in args if not a.startswith("-")]
    if not rest:
        raise PolicyError("docker ohne Unterbefehl")
    sub = rest[0]
    if sub == "compose":
        if len(rest) < 2 or rest[1] not in {"ps", "ls", "config", "images", "top"}:
            raise PolicyError("docker compose nur ps/ls/images/top")
        return
    if sub in {"network", "volume", "image", "container"}:
        if len(rest) < 2 or rest[1] not in {"ls", "list"}:
            raise PolicyError(f"docker {sub} nur ls")
        return
    if sub not in {"ps", "logs", "images", "stats", "version", "info", "top", "port", "events"}:
        raise PolicyError("docker nur ps/logs/images/stats/version/info/top/port")
    if sub in {"logs", "events"} and any(a in ("-f", "--follow") for a in args):
        raise PolicyError("docker logs -f läuft endlos — ohne -f")
    if sub == "stats" and "--no-stream" not in args:
        raise PolicyError("docker stats nur mit --no-stream")


def _kubectl(args: list[str]) -> None:
    rest = [a for a in args if not a.startswith("-")]
    if not rest or rest[0] not in {"get", "describe", "logs", "top", "version", "events", "api-resources"}:
        raise PolicyError("kubectl nur get/describe/logs/top/version/events")
    if any(r.lower().startswith("secret") for r in rest[1:]):
        raise PolicyError("Secrets werden nicht gelesen")
    if rest[0] == "logs" and any(a in ("-f", "--follow") for a in args):
        raise PolicyError("kubectl logs ohne -f")


def _ip(args: list[str]) -> None:
    rest = [a for a in args if not a.startswith("-")]
    if not rest or rest[0] not in {"a", "addr", "address", "r", "route", "l", "link", "n", "neigh", "rule", "-br"}:
        raise PolicyError("ip nur addr/route/link/neigh")
    if len(rest) > 1 and rest[1] not in {"show", "list", "ls", "get", "dev"} and not rest[1].startswith(("eth", "en", "vmbr", "bond", "lo")):
        raise PolicyError("ip nur anzeigen (show)")


def _sed(args: list[str]) -> None:
    if "-n" not in args or any(a.startswith("-i") or a == "--in-place" for a in args):
        raise PolicyError("sed nur als 'sed -n <von>,<bis>p'")
    scripts = [a for a in args if not a.startswith("-")]
    if not scripts or not re.fullmatch(r"\d+(,\d+)?p|\$p|/[^/]{1,80}/p", scripts[0]):
        raise PolicyError("sed nur mit Zeilen-/Muster-Ausgabe (…p)")


def _find(args: list[str]) -> None:
    bad = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
    if bad & set(args):
        raise PolicyError("find ohne -exec/-delete/-fprint")


def _curl(args: list[str]) -> None:
    _deny_flags("-o", "-O", "-T", "-d", "-F", "-K", "--output", "--remote-name", "--upload-file",
                "--data", "--data-raw", "--data-binary", "--data-urlencode", "--form", "--config")(args)
    for i, a in enumerate(args):
        if a in ("-X", "--request") and (i + 1 >= len(args) or args[i + 1].upper() not in ("GET", "HEAD")):
            raise PolicyError("curl nur GET/HEAD")
        if a.startswith("--request=") and a.split("=", 1)[1].upper() not in ("GET", "HEAD"):
            raise PolicyError("curl nur GET/HEAD")


def _ping(args: list[str]) -> None:
    for i, a in enumerate(args):
        if a == "-c" and i + 1 < len(args) and args[i + 1].isdigit() and int(args[i + 1]) <= 5:
            return
    raise PolicyError("ping nur mit -c (höchstens 5)")


def _top(args: list[str]) -> None:
    if not any(a.startswith("-b") or a == "-bn1" for a in args) or not any("n" in a for a in args if a.startswith("-")):
        raise PolicyError("top nur als 'top -bn1'")


def _uniq(args: list[str]) -> None:
    if len([a for a in args if not a.startswith("-")]) > 1:
        raise PolicyError("uniq mit Ausgabedatei ist nicht erlaubt")


def _date(args: list[str]) -> None:
    if any(not a.startswith("+") and a not in ("-u", "-R", "--utc", "-I", "--iso-8601") for a in args):
        raise PolicyError("date nur zum Anzeigen")


def _dpkg(args: list[str]) -> None:
    if not args or args[0] not in {"-l", "-s", "-L", "-S", "--list", "--status", "--listfiles", "--search", "-p"}:
        raise PolicyError("dpkg nur -l/-s/-L/-S")


def _rpm(args: list[str]) -> None:
    if not args or not args[0].startswith("-q"):
        raise PolicyError("rpm nur -q…")


_ANY = lambda args: None  # noqa: E731

PROGRAMS = {
    # Dateien und Text ansehen
    "cat": _ANY, "head": _ANY, "tail": _deny_flags("-f", "-F", "--follow"), "less": None,
    "grep": _ANY, "egrep": _ANY, "fgrep": _ANY, "zgrep": _ANY, "zcat": _ANY, "wc": _ANY,
    "sort": _deny_flags("-o", "--output"), "uniq": _uniq, "cut": _ANY, "tr": _ANY,
    "column": _ANY, "jq": _ANY, "ls": _ANY, "stat": _ANY, "file": _ANY, "readlink": _ANY,
    "realpath": _ANY, "md5sum": _ANY, "sha256sum": _ANY, "echo": _ANY, "true": _ANY,
    "sed": _sed, "find": _find, "nl": _ANY, "basename": _ANY, "dirname": _ANY,
    # Systemzustand
    "df": _ANY, "du": _ANY, "free": _ANY, "uptime": _ANY, "uname": _ANY, "id": _ANY,
    "whoami": _no_args, "who": _ANY, "w": _ANY, "last": _ANY, "lsblk": _ANY, "lsof": _ANY,
    "ps": _ANY, "pstree": _ANY, "nproc": _ANY, "lscpu": _ANY, "lsmem": _ANY, "vmstat": _ANY,
    "iostat": _ANY, "mpstat": _ANY, "findmnt": _ANY, "hostname": _no_args, "date": _date,
    "dmesg": _deny_flags("-C", "-c", "-n", "-D", "-E", "--clear", "--console-off", "--console-on",
                         "--console-level", "--read-clear"),
    "top": _top, "getent": _ANY, "dig": _ANY, "nslookup": _ANY, "host": _ANY, "ping": _ping,
    "ss": _ANY, "netstat": _ANY, "ip": _ip, "curl": _curl, "openssl": None,
    # Dienste und Logs
    "systemctl": _sub({"status", "show", "is-active", "is-enabled", "is-failed", "list-units",
                       "list-unit-files", "list-timers", "cat", "list-dependencies", "list-sockets"}),
    "journalctl": _deny_flags("-f", "--follow", "--vacuum-size", "--vacuum-time", "--vacuum-files",
                              "--rotate", "--flush", "--sync", "--setup-keys", "--relinquish-var",
                              "--update-catalog"),
    # Container, Virtualisierung, Speicher
    "docker": _docker, "podman": _docker, "kubectl": _kubectl,
    "zpool": _sub({"list", "status", "iostat", "get", "history"}),
    "zfs": _sub({"list", "get"}),
    "pvesh": _sub({"get", "ls"}), "qm": _sub({"list", "status", "config", "pending"}),
    "pct": _sub({"list", "status", "config"}), "pvecm": _sub({"status", "nodes"}),
    "pveversion": _ANY, "lvs": _ANY, "vgs": _ANY, "pvs": _ANY,
    # Pakete
    "dpkg": _dpkg, "dpkg-query": _ANY, "rpm": _rpm,
    "apt-cache": _sub({"policy", "show", "showpkg", "depends", "rdepends", "search"}),
}
PROGRAMS = {k: v for k, v in PROGRAMS.items() if v is not None}


def check(command: str) -> None:
    """Akzeptiert die Zeile als reine Leseoperation — oder PolicyError mit Grund."""
    if not command or not command.strip():
        raise PolicyError("Leerer Befehl")
    if len(command) > MAX_LEN or any(c in command for c in "\x00\n\r"):
        raise PolicyError("Befehl zu lang oder mehrzeilig")
    stripped = _ALLOWED_REDIRECTS.sub(" ", command)
    _scan(stripped)
    for seg in _segments(stripped):
        argv = _unwrap(seg)
        prog = _prog(argv[0])
        checker = PROGRAMS.get(prog)
        if checker is None:
            raise PolicyError(f"'{prog}' steht nicht auf der Lese-Liste")
        args = argv[1:]
        for a in args:
            if _SENSITIVE.search(a):
                raise PolicyError(f"Sensibler Pfad/Name '{a[:60]}' wird nicht gelesen")
        checker(args)


def is_read_only(command: str) -> bool:
    try:
        check(command)
        return True
    except PolicyError:
        return False
