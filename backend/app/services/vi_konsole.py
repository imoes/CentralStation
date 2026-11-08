"""Die Konsole (claude_cli) an die lokale VI anschließen: MCP-Server „vi“ + vi-mcp-Hook.

Nutzer 08.11.2026: Die Konsole rief für Wissen nur search_knowledge/store_knowledge (Index cs-knowledge) auf — die
VI kannte sie nicht. Entscheidung: „Lokale VI + Hook“ — die lokale vi-mcp-Instanz in CentralStation (Compose-Dienst
vi-mcp) als MCP-Server, dazu der Hook aus dem vi-mcp-Paket: vor jedem Werkzeug vi_vorher (Hinweise), danach vi_nachher.
Instanz „konsole“ (nach System benannt, nicht nach Person).

Konfiguration (global_settings): vi.konsole_url (Klartext), vi.konsole_token (verschlüsselt). Fehlt eins, ist die VI
für die Konsole aus — und zwar benannt: Server und Hook werden dann entfernt, nicht still stehen gelassen.
Hook: app/resources/vi_hook.py = /usr/share/vi-mcp/hook/vi_hook.py aus dem Paket v0.14.3 (unverändert).
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from urllib.parse import urlparse

URL_SCHLUESSEL = "vi.konsole_url"
TOKEN_SCHLUESSEL = "vi.konsole_token"
SERVER_NAME = "vi"
#: Name des Compose-Dienstes — gehört in die Proxy-Ausnahmen der Konsolen-Container (sonst Squid → 503)
STANDARD_HOST = "vi-mcp"
HOOK_QUELLE = Path(__file__).resolve().parent.parent / "resources" / "vi_hook.py"
#: Ereignisse laut docs/HOOK.md des Pakets (UserPromptSubmit bräuchte eine eigene Instanz mit Mensch-Recht — nicht hier)
EREIGNISSE = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionEnd")
KENNUNG = "vi_hook.py"


def pfade(home: str) -> dict:
    """Wo Hook, Token und Zustand im Konsolen-Container liegen. Rein."""
    basis = f"{home}/.config/vi-mcp"
    return {"verzeichnis": basis, "hook": f"{basis}/{KENNUNG}", "token": f"{basis}/token-konsole",
            "zustand": f"{home}/.cache/vi-mcp/hook"}


def hook_befehl(home: str, url: str) -> str:
    """Befehlszeile des Hooks — Umgebung inline, damit sie nicht von der Sitzung abhängt. Rein."""
    p = pfade(home)
    env = {"VI_MCP_URL": url, "VI_MCP_TOKEN_FILE": p["token"], "VI_HOOK_STATE": p["zustand"]}
    return "env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + f" python3 -I {shlex.quote(p['hook'])}"


def mische_einstellungen(alt: dict, befehl: str | None) -> dict:
    """settings.json: frühere vi-Hooks entfernen, neue eintragen (befehl=None → nur entfernen). Andere Hooks bleiben. Rein."""
    neu = dict(alt or {})
    hooks = {k: list(v) for k, v in (neu.get("hooks") or {}).items()}
    for ereignis in list(hooks):
        hooks[ereignis] = [e for e in hooks[ereignis] if KENNUNG not in json.dumps(e)]
        if not hooks[ereignis]:
            del hooks[ereignis]
    if befehl:
        for ereignis in EREIGNISSE:
            eintrag: dict = {"hooks": [{"type": "command", "command": befehl,
                                        **({} if ereignis == "SessionEnd" else {"timeout": 5})}]}
            if ereignis != "SessionEnd":
                eintrag = {"matcher": "*", **eintrag}
            hooks.setdefault(ereignis, []).append(eintrag)
    if hooks:
        neu["hooks"] = hooks
    else:
        neu.pop("hooks", None)
    return neu


def host_von(url: str | None) -> str:
    return (urlparse(url).hostname if url else None) or STANDARD_HOST


async def lade(db) -> dict | None:
    """{url, token} der Konsolen-Instanz, oder None (VI für die Konsole aus)."""
    from app.services.settings import get_setting
    url = (await get_setting(db, URL_SCHLUESSEL) or "").strip()
    token = (await get_setting(db, TOKEN_SCHLUESSEL) or "").strip()
    if not url or not token:
        return None
    return {"url": url, "token": token}
