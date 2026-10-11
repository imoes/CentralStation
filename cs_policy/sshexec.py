"""Einen bereits geprüften Befehl per SSH auf einem Host ausführen.

Die Prüfung, WAS laufen darf, liegt woanders (templates.render, später die
Positivliste der Schleuse). Hier geht es nur um das Wie: Host validieren, ssh ohne
Shell-Interpretation lokal aufrufen, Zeit und Ausgabe begrenzen, bei Zeitüberschreitung
die ganze Prozessgruppe beenden, und die Fehlerarten unterscheidbar zurückgeben
("ssh fehlgeschlagen" ist etwas anderes als "Befehl lieferte nichts").
"""
from __future__ import annotations

import ipaddress
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field

from cs_policy.templates import PolicyError

_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
#: Ziele, die nie über diesen Weg erreicht werden: der Container selbst und der
#: Docker-Host, über den gesprungen wird — dort liegt der Docker-Socket. Dessen Namen
#: sind Deployment-Daten, nicht Quellcode: CS_SSH_DENIED_HOSTS (Komma-Liste) in .env.
DENIED_HOSTS = frozenset({"localhost", "host.docker.internal"} | {
    h.strip().lower() for h in os.getenv("CS_SSH_DENIED_HOSTS", "").split(",") if h.strip()})

SSH_EXIT_ERROR = 255          # ssh selbst gescheitert (Verbindung, Authentifizierung)


def check_host(host: str, denied: frozenset[str] = DENIED_HOSTS) -> str:
    """Hostname prüfen und normalisieren — oder PolicyError.

    Kein user@, keine Optionen ("-oProxyCommand=…"), keine Loopback-/Link-Local-Adressen.
    """
    h = (host or "").strip().lower().rstrip(".")
    if not _HOST.fullmatch(h):
        raise PolicyError(f"Ungültiger Host {host!r}")
    if h in denied or h.split(".")[0] in denied:
        raise PolicyError(f"Host {h} ist als Ziel nicht erlaubt")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return h
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise PolicyError(f"Adresse {h} ist als Ziel nicht erlaubt")
    return h


@dataclass
class SshResult:
    host: str
    command: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False
    duration_ms: int = 0
    #: None = Befehl lief (exit_code sagt, wie); sonst "ssh_failed" | "timeout" | "spawn_failed"
    error: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0


def ssh_argv(host: str, command: str, *, ssh_binary: str = "ssh", config: str | None = None,
             identity: str | None = None, connect_timeout: int = 10) -> list[str]:
    argv = [ssh_binary]
    if config:
        argv += ["-F", config]
    if identity:
        argv += ["-i", identity, "-o", "IdentitiesOnly=yes"]
    argv += [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-o", "ClearAllForwardings=yes",
        "-o", "ForwardAgent=no",
        "-o", "PermitLocalCommand=no",
        "-o", "RequestTTY=no",
        "-o", "ControlMaster=no",
        "-T",
        "--", host, command,
    ]
    return argv


def run_ssh(host: str, command: str, *, timeout: float = 60, max_bytes: int = 256 * 1024,
            **argv_opts) -> SshResult:
    """Synchron ausführen (für asyncio: asyncio.to_thread(run_ssh, …))."""
    host = check_host(host)
    res = SshResult(host=host, command=command)
    argv = ssh_argv(host, command, **argv_opts)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        res.error, res.stderr = "spawn_failed", str(exc)
        return res
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        res.timed_out, res.error = True, "timeout"
    res.duration_ms = int((time.monotonic() - t0) * 1000)
    res.exit_code = proc.returncode
    if len(out) > max_bytes:
        out, res.truncated = out[:max_bytes], True
    res.stdout = out.decode("utf-8", errors="replace")
    res.stderr = err[:16 * 1024].decode("utf-8", errors="replace")
    if res.error is None and proc.returncode == SSH_EXIT_ERROR:
        res.error = "ssh_failed"
    return res
