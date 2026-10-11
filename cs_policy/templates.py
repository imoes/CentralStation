"""Lese-Vorlagen: die EINZIGEN Befehle, die ohne Freigabe auf Servern laufen.

"Zu 100 % lesend" wird hier nicht durch das Erkennen schädlicher Befehle erreicht,
sondern umgekehrt: es gibt nur diese Vorlagen. Wer sie aufruft (die KI eingeschlossen),
wählt eine Vorlage aus und setzt Parameter ein; jeder Parameter muss sein Muster
vollständig erfüllen und wird zusätzlich shell-gequotet. Freien Befehlstext nimmt
render() nicht an — was hier nicht steht, kann nicht laufen.

Bewusst NICHT enthalten: `<befehl> --help`. Programme, die --help ignorieren (auch
eigene Skripte im PATH), würden dabei schlicht ausgeführt.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field


class PolicyError(ValueError):
    """Vorlage unbekannt oder ein Parameter erfüllt sein Muster nicht."""


@dataclass(frozen=True)
class Param:
    pattern: str
    required: bool = True
    default: str | None = None
    hint: str = ""

    def check(self, name: str, value) -> str | None:
        if value is None or value == "":
            if self.required and self.default is None:
                raise PolicyError(f"Parameter '{name}' fehlt")
            return self.default
        text = str(value)
        if not re.fullmatch(self.pattern, text):
            raise PolicyError(f"Parameter '{name}' ungültig: {text[:60]!r} ({self.hint or self.pattern})")
        return text


@dataclass(frozen=True)
class Template:
    id: str
    description: str
    command: str                          # mit {param}-Platzhaltern, Werte werden gequotet
    params: dict[str, Param] = field(default_factory=dict)
    #: Darf ein Konsolen-Agent diese Vorlage über die MCP-Werkzeuge aufrufen? Alle
    #: Vorlagen laufen mit dem System-Account; für Agenten bleibt es bei Dokumentation
    #: und Fähigkeiten des Hosts, damit niemand darüber Logs eines Hosts liest, die er
    #: mit seinem eigenen Zugang nicht sähe.
    agent_allowed: bool = False


_NAME = r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}"          # Befehl/Manpage/Paket
_UNIT = r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,127}"        # systemd-Unit
_CONTAINER = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"     # Docker-Container
_SINCE = r"-[0-9]{1,4}(min|h|d)"                     # journalctl relativ, z. B. -30min
_LINES = r"[1-9][0-9]{0,2}|1000"                     # 1..1000

_TOOLS = ("systemctl journalctl docker podman kubectl crictl nginx apache2 httpd haproxy "
          "mysql psql redis-cli java python3 node php ansible zfs zpool lvs pvesh qm pct")

TEMPLATES: dict[str, Template] = {t.id: t for t in [
    Template("man_search", "Manpages zu einem Stichwort suchen (Abschnitte 1 und 8)",
             "apropos -s 1,8 -- {query} 2>/dev/null | head -n 80",
             {"query": Param(_NAME, hint="ein Wort, z. B. rsync")}, agent_allowed=True),
    Template("man_read", "Eine Manpage lesen (als Text)",
             "MANWIDTH=100 man -P cat {section} -- {page} 2>/dev/null | col -b",
             {"page": Param(_NAME, hint="Befehl, z. B. rsync"),
              "section": Param(r"[1-9][a-z]{0,3}", required=False, default="", hint="z. B. 8")},
             agent_allowed=True),
    Template("host_capabilities", "Was ist auf dem Host vorhanden: OS, Werkzeuge, Rechte",
             "cat /etc/os-release 2>/dev/null; echo '---uname'; uname -srm; "
             "echo '---id'; id; echo '---tools'; for t in " + _TOOLS + "; do "
             "p=$(command -v \"$t\" 2>/dev/null) && echo \"$t $p\"; done; "
             "echo '---sudo'; sudo -n -l 2>/dev/null | head -n 30",
             {}, agent_allowed=True),
    Template("disk_usage", "Belegung der Dateisysteme",
             "df -hP -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null"),
    Template("memory", "Arbeitsspeicher und Last", "free -m; uptime"),
    Template("service_status", "Status einer systemd-Unit",
             "systemctl status --no-pager --lines=50 -- {unit}",
             {"unit": Param(_UNIT, hint="z. B. nginx.service")}),
    Template("journal_tail", "Letzte Journal-Zeilen einer Unit",
             "journalctl --no-pager -u {unit} --since {since} -n {lines}",
             {"unit": Param(_UNIT, hint="z. B. nginx.service"),
              "since": Param(_SINCE, required=False, default="-60min", hint="z. B. -30min"),
              "lines": Param(_LINES, required=False, default="200", hint="1..1000")}),
    Template("docker_ps", "Laufende Container",
             # {{{{…}}}}: str.format macht daraus Dockers {{…}}.
             "docker ps --format '{{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Status}}}}' 2>/dev/null "
             "|| sudo -n docker ps --format '{{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Status}}}}'"),
    Template("container_logs", "Letzte Log-Zeilen eines Containers",
             "docker logs --tail {lines} -- {name} 2>&1 || sudo -n docker logs --tail {lines} -- {name} 2>&1",
             {"name": Param(_CONTAINER, hint="Container-Name"),
              "lines": Param(_LINES, required=False, default="200", hint="1..1000")}),
    Template("listening_ports", "Lauschende TCP-Ports", "ss -tlnH 2>/dev/null || netstat -tln"),
    Template("process_top", "Prozesse nach CPU",
             "ps -eo pid,user,%cpu,%mem,etime,comm --sort=-%cpu | head -n 25"),
    Template("package_version", "Installierte Version eines Pakets",
             "dpkg-query -W -f='${{Package}} ${{Version}}\\n' -- {package} 2>/dev/null "
             "|| rpm -q -- {package}",
             {"package": Param(r"[a-z0-9][a-z0-9.+-]{0,63}", hint="Paketname")}),
]}


def render(template_id: str, params: dict | None = None) -> str:
    """Den fertigen Befehl einer Vorlage bauen — oder PolicyError.

    Unbekannte Parameter sind ein Fehler (kein stilles Ignorieren): wer z. B.
    {"page": "ls", "extra": "; rm -rf /"} schickt, soll das merken.
    """
    tpl = TEMPLATES.get(template_id)
    if tpl is None:
        raise PolicyError(f"Unbekannte Vorlage '{template_id}'")
    params = dict(params or {})
    unknown = set(params) - set(tpl.params)
    if unknown:
        raise PolicyError(f"Unbekannte Parameter für '{template_id}': {', '.join(sorted(unknown))}")
    values: dict[str, str] = {}
    for name, spec in tpl.params.items():
        v = spec.check(name, params.get(name))
        values[name] = shlex.quote(v) if v else ""
    return tpl.command.format(**values)


def agent_templates() -> dict[str, Template]:
    return {k: t for k, t in TEMPLATES.items() if t.agent_allowed}


def describe() -> list[dict]:
    """Für Prompts/Oberflächen: welche Vorlagen es gibt und welche Parameter sie nehmen."""
    return [{"id": t.id, "description": t.description,
             "params": {n: {"required": p.required and p.default is None, "hint": p.hint}
                        for n, p in t.params.items()}}
            for t in TEMPLATES.values()]
