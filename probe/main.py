"""Prüf-Container: Lese-Vorlagen per SSH ausführen — sonst nichts.

API (nur im internen Docker-Netz):
  POST /configure {ssh_user, private_key}           Kopfzeile X-Probe-Token
  POST /probe     {host, template, params, timeout?} Kopfzeile X-Probe-Token
  GET  /health    {configured, user, fingerprint}

Sicherheit:
- Befehle entstehen ausschließlich über cs_policy.templates.render (Positivliste).
- Den System-Account (Nutzer + Schlüssel) stellt der Administrator in CentralStation ein;
  das Backend übergibt ihn per POST /configure. Hier liegt er nur im Container-Dateisystem,
  nicht in den Konsolen-Containern.
- Aufrufer müssen PROBE_SHARED_SECRET kennen (Backend).
- Ob ein Host überhaupt geprüft werden darf (Inventar) und wer fragt, entscheidet und
  protokolliert das Backend; hier wird zusätzlich die Host-Syntax geprüft.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from cs_policy.sshexec import run_ssh
from cs_policy.templates import PolicyError, render

log = logging.getLogger("probe")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SECRET = os.getenv("PROBE_SHARED_SECRET", "")
JUMP_HOST = os.getenv("PROBE_JUMP_HOST", "host.docker.internal")
SSH_DIR = "/root/.ssh"
SSH_CONFIG = f"{SSH_DIR}/config"
KEY = f"{SSH_DIR}/probe_key"
MAX_PARALLEL = int(os.getenv("PROBE_MAX_PARALLEL", "8"))
TIMEOUT_MAX = 120

app = FastAPI(title="CentralStation probe runner")
_sem = asyncio.Semaphore(MAX_PARALLEL)
#: Vom Backend gesetzt (POST /configure) — der Account wird vom Administrator in den
#: Einstellungen gepflegt, nicht in einer Datei dieses Containers. Nach einem Neustart
#: ist er leer; das Backend erkennt das am Fingerabdruck und schickt ihn erneut.
_state: dict = {"user": "", "fingerprint": ""}


def _domains() -> list[str]:
    raw = os.getenv("CS_INTERNAL_DOMAINS", "")
    return [d.strip() for d in raw.split(",") if d.strip() and d.strip() not in ("internal", "local")]


def fingerprint(user: str, key: str) -> str:
    return hashlib.sha256(f"{user}\n{key.strip()}".encode()).hexdigest()[:16]


def _write_ssh_config(user: str, key: str) -> None:
    os.makedirs(SSH_DIR, mode=0o700, exist_ok=True)
    fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key.strip() + "\n")
    lines = [
        f"Host {JUMP_HOST}",
        f"    User {user}",
        f"    IdentityFile {KEY}",
        "    ProxyJump none",
        "    StrictHostKeyChecking accept-new",
        "",
    ]
    patterns = " ".join(f"*.{d}" for d in _domains())
    if patterns:
        # Interne Namen löst nur der Docker-Host auf (sssd) — wie in den Konsolen-Containern.
        lines += [f"Host {patterns}", f"    ProxyJump {user}@{JUMP_HOST}", ""]
    lines += ["Host *", f"    User {user}", f"    IdentityFile {KEY}", "    IdentitiesOnly yes",
              "    StrictHostKeyChecking accept-new", f"    UserKnownHostsFile {SSH_DIR}/known_hosts",
              "    ConnectTimeout 10", ""]
    with open(SSH_CONFIG, "w") as fh:
        fh.write("\n".join(lines))
    os.chmod(SSH_CONFIG, 0o600)


def _check_token(token: str) -> None:
    if not SECRET or not hmac.compare_digest(SECRET, token or ""):
        raise HTTPException(403, "invalid probe token")


class ConfigureBody(BaseModel):
    ssh_user: str
    private_key: str


@app.post("/configure")
def configure(body: ConfigureBody, x_probe_token: str = Header(default="")):
    _check_token(x_probe_token)
    user = body.ssh_user.strip()
    if not re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}", user):
        raise HTTPException(422, "ungültiger SSH-Nutzer")
    if "PRIVATE KEY" not in body.private_key:
        raise HTTPException(422, "kein privater Schlüssel im PEM/OpenSSH-Format")
    _write_ssh_config(user, body.private_key)
    _state.update(user=user, fingerprint=fingerprint(user, body.private_key))
    log.info("probe runner configured by backend: user=%s jump=%s domains=%s", user, JUMP_HOST, _domains())
    return {"configured": True, "user": user, "fingerprint": _state["fingerprint"]}


class ProbeBody(BaseModel):
    host: str
    template: str
    params: dict = {}
    timeout: int = 60


@app.get("/health")
def health():
    return {"ok": True, "configured": bool(_state["fingerprint"]), "user": _state["user"],
            "fingerprint": _state["fingerprint"]}


@app.post("/probe")
async def probe(body: ProbeBody, x_probe_token: str = Header(default="")):
    _check_token(x_probe_token)
    if not _state["fingerprint"]:
        raise HTTPException(503, "probe runner not configured (no SSH account)")
    try:
        command = render(body.template, body.params)
    except PolicyError as exc:
        raise HTTPException(422, str(exc))
    timeout = max(5, min(TIMEOUT_MAX, body.timeout))
    async with _sem:
        try:
            res = await asyncio.to_thread(run_ssh, body.host, command, timeout=timeout,
                                          config=SSH_CONFIG)
        except PolicyError as exc:
            raise HTTPException(422, str(exc))
    log.info("probe %s %s on %s → exit=%s error=%s %dms", body.template,
             sorted(body.params), res.host, res.exit_code, res.error, res.duration_ms)
    return {"host": res.host, "template": body.template, "command": command,
            "exit_code": res.exit_code, "error": res.error, "stdout": res.stdout,
            "stderr": res.stderr, "truncated": res.truncated, "timed_out": res.timed_out,
            "duration_ms": res.duration_ms}
