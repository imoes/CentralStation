#!/usr/bin/env python3
"""SSH-Schleuse der Konsole (getrennter Modus): root hält den Schlüssel, der Agent nicht.

Läuft als root im Konsolen-Container. Der Agent (uid 1001) erreicht Server NUR hierüber:
Unix-Socket /run/cs-gate/gate.sock (root:agent 0660), der Aufrufer wird per SO_PEERCRED
geprüft. Eine Anfrage = eine JSON-Zeile, eine Antwort = eine JSON-Zeile.

  {"op": "exec",  "host": "...", "command": "...", "timeout": 120}
  {"op": "fetch", "host": "...", "path": "/var/log/x.log"}      (scp host:pfad lokal)

Ohne Freigabe läuft nur, was cs_policy.remote_allowlist als lesend akzeptiert. Die
Freigabe ist (bis zur Aktionsliste) das Zeitfenster /opt/cs-write-approval.json — es
gehört root; ohne sudo kann der Agent es nicht fälschen. Jede Entscheidung landet in
/var/log/cs-gate/audit.jsonl (root 0600).
"""
from __future__ import annotations

import base64
import json
import os
import shlex
import socket
import socketserver
import struct
import sys
import threading
import time

sys.path.insert(0, "/opt/cs-gate")
from cs_policy.remote_allowlist import check  # noqa: E402
from cs_policy.sshexec import check_host, run_ssh  # noqa: E402
from cs_policy.templates import PolicyError  # noqa: E402

SOCK_DIR = "/run/cs-gate"
SOCK = f"{SOCK_DIR}/gate.sock"
SSH_CONFIG = "/etc/cs-ssh/config"
APPROVAL = "/opt/cs-write-approval.json"
AUDIT = "/var/log/cs-gate/audit.jsonl"
AGENT_UID = 1001
MAX_PARALLEL = 4
FETCH_MAX = 50 * 1024 * 1024
_sem = threading.BoundedSemaphore(MAX_PARALLEL)
_audit_lock = threading.Lock()


def audit(**entry) -> None:
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _audit_lock:
        fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def approved() -> dict | None:
    try:
        with open(APPROVAL) as fh:
            data = json.load(fh)
        return data if float(data.get("expires_at", 0)) > time.time() else None
    except Exception:  # noqa: BLE001
        return None


def peer_uid(conn: socket.socket) -> int:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


class Handler(socketserver.StreamRequestHandler):
    def reply(self, obj: dict) -> None:
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())

    def handle(self) -> None:
        uid = peer_uid(self.connection)
        if uid != AGENT_UID:
            audit(decision="reject_peer", uid=uid)
            self.reply({"error": "peer", "reason": "nur für den Konsolen-Agenten"})
            return
        try:
            req = json.loads(self.rfile.readline(64 * 1024) or b"{}")
        except ValueError:
            self.reply({"error": "bad_request", "reason": "kein JSON"})
            return
        op = req.get("op")
        try:
            host = check_host(str(req.get("host") or ""))
        except PolicyError as exc:
            self.reply({"error": "denied", "reason": str(exc)})
            return
        if op == "exec":
            self.do_exec(host, str(req.get("command") or ""), int(req.get("timeout") or 120))
        elif op == "fetch":
            self.do_fetch(host, str(req.get("path") or ""))
        else:
            self.reply({"error": "bad_request", "reason": "op muss exec oder fetch sein"})

    def do_exec(self, host: str, command: str, timeout: int) -> None:
        decision = "read"
        try:
            check(command)
        except PolicyError as exc:
            grant = approved()
            if not grant:
                audit(decision="deny", host=host, command=command[:600], reason=str(exc))
                self.reply({"error": "denied", "reason": (
                    f"Kein reiner Lesebefehl ({exc}). Ändernde Befehle laufen nur mit Freigabe des "
                    "Nutzers in der CentralStation-Konsole — eine Zustimmung im Chat genügt nicht.")})
                return
            decision = f"approved:{grant.get('granted_by', '?')}"
        with _sem:
            res = run_ssh(host, command, timeout=max(5, min(600, timeout)), config=SSH_CONFIG,
                          max_bytes=1024 * 1024)
        audit(decision=decision, host=host, command=command[:600], exit=res.exit_code,
              error=res.error, ms=res.duration_ms)
        self.reply({"exit": res.exit_code, "stdout": res.stdout, "stderr": res.stderr,
                    "error": res.error, "truncated": res.truncated, "decision": decision})

    def do_fetch(self, host: str, path: str) -> None:
        if not path.startswith("/") or "\x00" in path or "\n" in path:
            self.reply({"error": "denied", "reason": "absoluter Pfad erforderlich"})
            return
        command = f"cat -- {shlex.quote(path)}"
        try:
            check(command)       # sensible Pfade sind auch beim Holen gesperrt
        except PolicyError as exc:
            audit(decision="deny", host=host, command=command, reason=str(exc))
            self.reply({"error": "denied", "reason": str(exc)})
            return
        with _sem:
            res = run_ssh(host, command, timeout=300, config=SSH_CONFIG, max_bytes=FETCH_MAX)
        audit(decision="fetch", host=host, path=path, exit=res.exit_code, bytes=len(res.stdout))
        # stdout ist hier Text (run_ssh dekodiert); für Binärdateien nicht geeignet — scp
        # vom Agenten ist für Logs/Configs gedacht.
        self.reply({"exit": res.exit_code, "error": res.error, "stderr": res.stderr,
                    "truncated": res.truncated,
                    "data": base64.b64encode(res.stdout.encode("utf-8", "surrogateescape")).decode()})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> None:
    os.makedirs("/var/log/cs-gate", mode=0o700, exist_ok=True)
    os.makedirs(SOCK_DIR, exist_ok=True)
    os.chown(SOCK_DIR, 0, AGENT_UID)
    os.chmod(SOCK_DIR, 0o750)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = Server(SOCK, Handler)
    os.chown(SOCK, 0, AGENT_UID)
    os.chmod(SOCK, 0o660)
    print("cs-ssh-gate: listening on", SOCK, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
