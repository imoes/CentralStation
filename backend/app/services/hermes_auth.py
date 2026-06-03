"""Hermes OAuth token reader.

Hermes (~/.hermes) stores OAuth tokens for providers like openai-codex
in ~/.hermes/auth.json. This module reads those tokens so CentralStation
can use them as Bearer auth without requiring a separate API key.

Usage:
  token = get_hermes_provider_token("openai-codex")
  # → the access_token string, or None if not authenticated

To authenticate (one-time, opens browser):
  hermes auth openai-codex
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_HERMES_AUTH_PATH = Path.home() / ".hermes" / "auth.json"


def _load_auth() -> dict:
    try:
        if _HERMES_AUTH_PATH.exists():
            return json.loads(_HERMES_AUTH_PATH.read_text())
    except Exception as e:
        log.debug("hermes_auth: could not read %s: %s", _HERMES_AUTH_PATH, e)
    return {}


def get_hermes_provider_token(provider: str = "openai-codex") -> str | None:
    """Return the OAuth access token for the given Hermes provider, or None."""
    auth = _load_auth()
    providers = auth.get("providers") or {}
    entry = providers.get(provider) or {}

    # Token may be nested differently depending on Hermes version
    token = (
        entry.get("access_token")
        or entry.get("token")
        or entry.get("api_key")
        or (entry.get("oauth") or {}).get("access_token")
    )
    return token or None


def get_hermes_provider_status(provider: str = "openai-codex") -> dict:
    """Return auth status for the given provider (for display in admin UI)."""
    auth = _load_auth()
    providers = auth.get("providers") or {}
    entry = providers.get(provider)

    if not entry:
        return {
            "authenticated": False,
            "provider": provider,
            "message": f"Nicht eingeloggt. Führe 'hermes auth {provider}' aus.",
        }

    token = get_hermes_provider_token(provider)
    expires_at = entry.get("expires_at") or entry.get("expiry") or "unbekannt"
    return {
        "authenticated": bool(token),
        "provider": provider,
        "expires_at": str(expires_at),
        "message": "OAuth-Token vorhanden" if token else "Token nicht lesbar",
    }


def list_hermes_providers() -> list[str]:
    """Return all provider names that have a token in Hermes auth."""
    auth = _load_auth()
    return list((auth.get("providers") or {}).keys())
