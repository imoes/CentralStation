#!/usr/bin/env python3
"""ssh/scp für den Konsolen-Agenten — spricht mit der Schleuse statt direkt mit dem Server.

Als `ssh` und `scp` im PATH des Agenten (/usr/local/lib/cs-agent-bin). Bequemlichkeit,
keine Grenze: wer /usr/bin/ssh direkt aufruft, hat keinen Schlüssel und kommt per
Netzwerkregel nicht an Port 22.

  ssh [-q -T -n -x -o Opt=Wert] [user@]host befehl…   → op exec
  scp [-q] host:/pfad lokales_ziel                     → op fetch (nur HOLEN)
"""
import base64
import json
import os
import socket
import sys

SOCK = "/run/cs-gate/gate.sock"
_SIMPLE = {"-q", "-T", "-n", "-x", "-4", "-6", "-C", "-tt", "-t"}
_SAFE_OPTS = ("ConnectTimeout=", "BatchMode=", "StrictHostKeyChecking=", "LogLevel=",
              "ServerAliveInterval=", "ServerAliveCountMax=")


def die(msg: str, code: int = 255) -> None:
    sys.stderr.write(f"cs-ssh: {msg}\n")
    sys.exit(code)


def call(req: dict) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(SOCK)
    except OSError as exc:
        die(f"SSH-Schleuse nicht erreichbar ({exc})")
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(1 << 20)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf or b"{}")


def strip_user(target: str) -> str:
    return target.split("@", 1)[1] if "@" in target else target


def ssh(argv: list[str]) -> None:
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        a = argv[i]
        if a in _SIMPLE:
            i += 1
        elif a == "-o" and i + 1 < len(argv) and argv[i + 1].startswith(_SAFE_OPTS):
            i += 2
        elif a.startswith("-o") and a[2:].startswith(_SAFE_OPTS):
            i += 1
        else:
            die(f"Option {a} wird über die Schleuse nicht unterstützt")
    if i >= len(argv):
        die("kein Host angegeben")
    host = strip_user(argv[i])
    command = " ".join(argv[i + 1:])
    if not command:
        die("interaktive Sitzungen sind für den Agenten nicht möglich — gib einen Befehl an")
    r = call({"op": "exec", "host": host, "command": command, "timeout": 300})
    if r.get("error") == "denied" or r.get("error") == "peer":
        die(r.get("reason", "abgelehnt"))
    sys.stdout.write(r.get("stdout") or "")
    sys.stderr.write(r.get("stderr") or "")
    if r.get("error") == "timeout":
        die("Zeitüberschreitung", 124)
    sys.exit(r.get("exit") if isinstance(r.get("exit"), int) else 255)


def scp(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("-")]
    if any(a.startswith("-") and a not in ("-q", "-p", "-C") for a in argv):
        die("scp über die Schleuse kennt nur -q/-p/-C")
    if len(args) != 2 or ":" not in args[0] or ":" in args[1]:
        die("nur HOLEN wird unterstützt: scp host:/pfad lokales_ziel — Hochladen braucht eine Freigabe")
    host, path = args[0].split(":", 1)
    r = call({"op": "fetch", "host": strip_user(host), "path": path})
    if r.get("error") in ("denied", "peer"):
        die(r.get("reason", "abgelehnt"))
    if r.get("exit") != 0:
        die(f"Holen fehlgeschlagen: {(r.get('stderr') or '').strip()[:300]}", 1)
    dest = args[1]
    if os.path.isdir(dest):
        dest = os.path.join(dest, os.path.basename(path))
    with open(dest, "wb") as fh:
        fh.write(base64.b64decode(r.get("data") or ""))


def main() -> None:
    prog = os.path.basename(sys.argv[0])
    if prog == "scp":
        scp(sys.argv[1:])
    elif prog in ("rsync", "sftp", "sshpass"):
        die(f"{prog} ist über die Schleuse nicht verfügbar — zum Holen: scp host:/pfad ziel")
    else:
        ssh(sys.argv[1:])


if __name__ == "__main__":
    main()
