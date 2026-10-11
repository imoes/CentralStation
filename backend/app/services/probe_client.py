"""Lese-Vorlagen über den Prüf-Container ausführen — mit Inventar-Prüfung und Protokoll.

Der Prüf-Container (probe/) führt nur Vorlagen aus cs_policy aus, mit dem System-SSH-
Account, den der Administrator einstellt (probe.ssh_user / probe.ssh_key) und den dieses
Modul dem Container übergibt. Hier entscheidet das Backend vorher, OB ein Host geprüft werden darf
(nur Hosts aus dem CheckMK-Inventar, plus CS_PROBE_EXTRA_HOSTS), und protokolliert
jeden Lauf in remote_exec_log.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from cs_policy.templates import TEMPLATES, PolicyError, render

log = logging.getLogger(__name__)

PROBE_URL = os.getenv("PROBE_URL", "http://probe-runner:8090").rstrip("/")
_INVENTORY_TTL = 900.0
_inventory: tuple[float, set[str]] = (0.0, set())


class ProbeError(RuntimeError):
    """kind: not_configured | host_unknown | refused | runner_error"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _extra_hosts() -> set[str]:
    return {h.strip().lower() for h in os.getenv("CS_PROBE_EXTRA_HOSTS", "").split(",") if h.strip()}


async def _inventory_hosts() -> set[str]:
    """Alle Hosts aller CheckMK-Instanzen (15 min zwischengespeichert)."""
    global _inventory
    ts, hosts = _inventory
    if hosts and time.monotonic() - ts < _INVENTORY_TTL:
        return hosts
    from app.services.checkmk_metrics import checkmk_configs
    from app.services.connectors.checkmk import CheckMKConnector
    found: set[str] = set()
    for cfg, creds in await checkmk_configs():
        try:
            for h in await CheckMKConnector(base_url=cfg.base_url, credentials=creds).get_all_hosts():
                if h.get("hostname"):
                    found.add(h["hostname"].lower())
        except Exception as exc:  # noqa: BLE001
            log.warning("probe inventory: CheckMK %s failed: %s", cfg.base_url, exc)
    if found:
        _inventory = (time.monotonic(), found)
    return found or hosts


async def resolve_host(host: str) -> str:
    """Host aus dem Inventar (FQDN oder eindeutiger Kurzname) — oder ProbeError."""
    from app.services.error_clusters.incident import host_matches
    want = (host or "").strip().lower().rstrip(".")
    if not want:
        raise ProbeError("host_unknown", "Kein Host angegeben")
    known = await _inventory_hosts() | _extra_hosts()
    if want in known:
        return want
    matches = sorted(h for h in known if host_matches(want, [h]))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ProbeError("host_unknown", f"'{host}' ist mehrdeutig: {', '.join(matches[:5])}")
    raise ProbeError("host_unknown", f"'{host}' ist nicht im CheckMK-Inventar — nur dort "
                                     "geführte Hosts werden geprüft")


async def run_probe(host: str, template: str, params: dict | None = None, *,
                    user_id: str | None = None, source: str = "mcp", agent: bool = False,
                    context: dict | None = None, timeout: int = 60) -> dict[str, Any]:
    """Vorlage auf einem Inventar-Host ausführen. Gibt das Ergebnis des Runners zurück.

    agent=True: nur Vorlagen, die für Konsolen-Agenten freigegeben sind (Doku, Fähigkeiten).
    """
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise ProbeError("refused", f"Unbekannte Vorlage '{template}'")
    if agent and not tpl.agent_allowed:
        raise ProbeError("refused", f"Vorlage '{template}' ist für Konsolen-Agenten nicht freigegeben")
    try:
        render(template, params)                    # früh prüfen, klare Meldung
    except PolicyError as exc:
        raise ProbeError("refused", str(exc)) from exc
    target = await resolve_host(host)
    secret = os.getenv("PROBE_SHARED_SECRET", "")
    if not secret:
        raise ProbeError("not_configured", "Prüf-Container nicht eingerichtet (PROBE_SHARED_SECRET fehlt)")
    try:
        async with httpx.AsyncClient(timeout=timeout + 15, trust_env=False) as c:
            await _ensure_configured(c, secret)
            r = await c.post(f"{PROBE_URL}/probe", headers={"X-Probe-Token": secret},
                             json={"host": target, "template": template, "params": params or {},
                                   "timeout": timeout})
    except httpx.HTTPError as exc:
        raise ProbeError("runner_error", f"Prüf-Container nicht erreichbar: {exc}") from exc
    if r.status_code == 503:
        raise ProbeError("not_configured", "Prüf-Container hat keinen SSH-Account eingerichtet")
    if r.status_code == 422:
        raise ProbeError("refused", r.json().get("detail", "abgelehnt"))
    if r.status_code != 200:
        raise ProbeError("runner_error", f"Prüf-Container: HTTP {r.status_code}")
    result = r.json()
    await _log(user_id, source, target, template, params or {}, result, context)
    return result


def account_fingerprint(user: str, key: str) -> str:
    """Muss probe/main.py fingerprint() entsprechen."""
    import hashlib
    return hashlib.sha256(f"{user}\n{key.strip()}".encode()).hexdigest()[:16]


async def _ensure_configured(client: httpx.AsyncClient, secret: str) -> None:
    """Den vom Administrator eingestellten Account an den Prüf-Container übergeben,
    falls dieser ihn (noch) nicht hat — nach einem Neustart oder einer Änderung."""
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_setting
    async with AsyncSessionLocal() as db:
        user = (await get_setting(db, "probe.ssh_user") or "").strip()
        key = await get_setting(db, "probe.ssh_key") or ""
    if not user or "PRIVATE KEY" not in key:
        raise ProbeError("not_configured", "Kein Prüf-Account eingestellt "
                                           "(Admin → KI → Prüf-Container: SSH-Nutzer und Schlüssel)")
    want = account_fingerprint(user, key)
    health = (await client.get(f"{PROBE_URL}/health")).json()
    if health.get("fingerprint") == want:
        return
    r = await client.post(f"{PROBE_URL}/configure", headers={"X-Probe-Token": secret},
                          json={"ssh_user": user, "private_key": key})
    if r.status_code != 200:
        raise ProbeError("not_configured", f"Prüf-Container lehnt den Account ab: "
                                           f"{r.json().get('detail', r.status_code)}")
    log.info("probe runner (re)configured with account %s", user)


async def _log(user_id, source, host, template, params, result, context) -> None:
    try:
        import uuid as _uuid
        from app.core.database import AsyncSessionLocal
        from app.models.remote_exec import RemoteExecLog
        async with AsyncSessionLocal() as db:
            db.add(RemoteExecLog(
                user_id=_uuid.UUID(user_id) if user_id else None, source=source, host=host,
                template=template, params=params, exit_code=result.get("exit_code"),
                error=result.get("error"), duration_ms=result.get("duration_ms"),
                output_bytes=len(result.get("stdout") or ""), context=context))
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — Protokoll darf den Lauf nicht kexample, aber laut
        log.warning("remote_exec_log write failed: %s", exc)
