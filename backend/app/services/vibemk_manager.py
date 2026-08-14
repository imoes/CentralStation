"""VibeMK (CheckMK MCP server) container lifecycle — credentials from the connector.

VibeMK reads its CheckMK credentials from the environment once at process start
(`CheckMKConfig.from_env()`), so it cannot pick up connector changes by itself. This
module owns the containers instead: it renders the environment from the SINGLE
CheckMK connector in the database and recreates a container whenever those
credentials change. There is deliberately no second copy of the CheckMK password.

Two instances exist, because the write restriction has to be topological rather
than advisory — an agent must not merely be *told* not to reconfigure monitoring:

    cs-vibemk         read + operational tools (77)   → every agent
    cs-vibemk-admin   all tools incl. configuration   → only users holding the
                                                        checkmk_admin permission

A user without the permission never receives the admin URL, so the configuration
tools are unreachable for their agent rather than just discouraged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

log = logging.getLogger(__name__)

VIBEMK_IMAGE = os.getenv("VIBEMK_IMAGE", "centralstation-vibemk:latest")
VIBEMK_NETWORK = os.getenv("VIBEMK_NETWORK", "centralstation_default")

#: Exposure tiers. TIER_ADMIN additionally gets the configuration tools.
TIER_DEFAULT = "default"
TIER_ADMIN = "admin"
TIERS = (TIER_DEFAULT, TIER_ADMIN)

#: Label carrying the fingerprint of the credentials a container was started with.
_FP_LABEL = "cs-vibemk-fp"


def _client():
    import docker
    return docker.from_env()


def container_name(tier: str) -> str:
    return "cs-vibemk-admin" if tier == TIER_ADMIN else "cs-vibemk"


def mcp_url(tier: str) -> str:
    """Internal MCP endpoint of the given tier (streamable-http)."""
    return f"http://{container_name(tier)}:8000/mcp"


async def checkmk_settings(db) -> tuple[dict | None, str]:
    """Load the CheckMK connector. Returns (settings, reason_when_unavailable).

    The reason is surfaced to callers so a missing/incomplete connector shows up as
    a named cause instead of a silently absent MCP server.
    """
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials as _dec

    res = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "checkmk",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    conn = res.scalar_one_or_none()
    if not conn:
        return None, "Kein aktivierter CheckMK-Konnektor konfiguriert"
    creds = _dec(conn.encrypted_credentials) or {}
    base_url, site = _split_site(conn.base_url or "", creds.get("site") or "")
    missing = [
        k for k, v in (
            ("base_url", base_url),
            ("username", creds.get("username")),
            ("password", creds.get("password")),
            ("site", site),
        ) if not v
    ]
    if missing:
        return None, (
            f"CheckMK-Konnektor unvollständig: {', '.join(missing)} fehlt "
            "(die Site darf auch im Pfad der Basis-URL stehen, z. B. https://host/mysite)"
        )
    return {
        "base_url": base_url,
        "username": creds.get("username", ""),
        "password": creds.get("password", ""),
        "site": site,
        "verify_ssl": creds.get("verify_ssl", True),
    }, ""


def _split_site(base_url: str, site: str) -> tuple[str, str]:
    """Split the connector's base URL into (server, site) as VibeMK expects them.

    CentralStation's own CheckMK connector accepts the site embedded in the URL path
    (e.g. https://monitoring.example.com/im) and leaves the `site` credential empty;
    VibeMK instead wants server and site separately. Rather than asking the admin to
    maintain the value twice — which is exactly the duplication this module exists to
    remove — derive the site from the path when the credential is absent.
    """
    from urllib.parse import urlparse

    base = (base_url or "").rstrip("/")
    site = (site or "").strip("/")
    if site or not base:
        return base, site
    parsed = urlparse(base)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return base, ""  # no site anywhere → reported as missing by the caller
    derived = parts[-1]
    server = base[: -(len(derived) + 1)] if base.endswith("/" + derived) else base
    return server.rstrip("/"), derived


def _env_for(tier: str, cmk: dict) -> dict:
    env = {
        "CHECKMK_SERVER_URL": cmk["base_url"],
        "CHECKMK_SITE": cmk["site"],
        "CHECKMK_USERNAME": cmk["username"],
        "CHECKMK_PASSWORD": cmk["password"],
        "CHECKMK_VERIFY_SSL": "true" if cmk.get("verify_ssl", True) else "false",
        "PORT": "8000",
    }
    if tier == TIER_ADMIN:
        env["VIBEMK_ALLOW_WRITE"] = "true"
    return env


def _fingerprint(env: dict) -> str:
    """Stable hash of the effective environment — changes force a recreate."""
    return hashlib.sha256(
        json.dumps(env, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _ensure_sync(tier: str, cmk: dict) -> str:
    """Create/refresh one tier's container. Returns its MCP URL."""
    cli = _client()
    name = container_name(tier)
    env = _env_for(tier, cmk)
    fp = _fingerprint(env)

    try:
        existing = cli.containers.get(name)
    except Exception:
        existing = None

    if existing is not None:
        stale = (existing.labels or {}).get(_FP_LABEL) != fp
        if stale:
            log.info("vibemk_manager: credentials changed → recreating %s", name)
            existing.remove(force=True)
            existing = None
        else:
            if existing.status != "running":
                existing.start()
            return mcp_url(tier)

    cli.containers.run(
        VIBEMK_IMAGE,
        name=name,
        detach=True,
        environment=env,
        network=VIBEMK_NETWORK,
        labels={"cs-vibemk": "1", "cs-vibemk-tier": tier, _FP_LABEL: fp},
        restart_policy={"Name": "unless-stopped"},
    )
    log.info("vibemk_manager: started %s (tier=%s)", name, tier)
    return mcp_url(tier)


async def ensure_vibemk(db, tier: str = TIER_DEFAULT) -> tuple[str | None, str]:
    """Ensure the tier's container runs with current connector credentials.

    Returns (mcp_url, reason). On failure mcp_url is None and reason explains why,
    so the caller can surface a cause rather than an unexplained missing server.
    """
    import asyncio

    if tier not in TIERS:
        return None, f"Unbekannte Stufe '{tier}'"
    cmk, reason = await checkmk_settings(db)
    if not cmk:
        return None, reason
    try:
        url = await asyncio.to_thread(_ensure_sync, tier, cmk)
        return url, ""
    except Exception as exc:  # noqa: BLE001
        log.warning("vibemk_manager: ensure failed for tier=%s: %s", tier, exc)
        return None, f"VibeMK-Container konnte nicht gestartet werden: {exc}"
