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
#: Ereignisse laut docs/HOOK.md des Pakets. UserPromptSubmit ab vi-mcp 0.44.0: vi_prompt_context liefert zu JEDER Frage
#: (auch Folgefragen) passende Hinweise, der Server speichert den Prompt nicht. Nutzer 11.11.2026 (über die vi-mcp-Sitzung):
#: „die VI sucht bei jeder Nutzerfrage“ — gemessen vorher: VI-Suche in 4/10 ersten Turns, aber nur 2/80 Folgeturns. Das
#: Korrektur-/Noten-Signal desselben Ereignisses braucht Mensch-Recht; fehlt es, verliert der Hook nur das Signal, nicht den Kontext.
#: Stop ab vi-mcp 0.59.0: einmal je Sitzung die Bitte um vi_feedback, wenn Hinweise gezeigt, aber nicht bewertet wurden
#: (gemessen von der vi-mcp-Sitzung: 52 Rückmeldungen auf 12 332 gezeigte Hinweise am 12.11.2026). Aus: VI_MCP_FEEDBACK_NUDGE=0.
EREIGNISSE = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd")
#: Ereignisse ohne Werkzeug — dort gibt es keinen matcher
OHNE_MATCHER = ("Stop", "SessionEnd")
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
            if ereignis not in OHNE_MATCHER:
                eintrag = {"matcher": "*", **eintrag}
            hooks.setdefault(ereignis, []).append(eintrag)
    if hooks:
        neu["hooks"] = hooks
    else:
        neu.pop("hooks", None)
    return neu


def host_von(url: str | None) -> str:
    return (urlparse(url).hostname if url else None) or STANDARD_HOST


def mcp_server(vi: dict) -> dict:
    """Eintrag für die MCP-Konfiguration von Hermes und Codex (Form wie extra_servers). Rein."""
    return {"transport": "streamable-http", "url": vi["url"], "headers": {"Authorization": f"Bearer {vi['token']}"}}


async def lerne(db, text: str, hashtags: list[str], quelle: str, wichtigkeit: int = 1, ausgang: str | None = None) -> str:
    """Erkenntnis in die lokale VI (Instanz konsole) — ersetzt die Spiegelung nach cs-knowledge.
    Ohne Konfiguration: benannt übersprungen, nicht still."""
    vi = await lade(db)
    if vi is None:
        return "übersprungen: VI für die Konsole nicht konfiguriert (vi.konsole_url/vi.konsole_token)"
    from app.services.connectors.vi_mcp import ViMcpConnector
    from app.services.log_watch.vi_bruecke import vi_tag
    tags = sorted({t for t in (vi_tag(h) for h in hashtags) if t})
    return await ViMcpConnector(vi["url"], {"token": vi["token"]}).lerne(text, tags, quelle, wichtigkeit, ausgang=ausgang)


async def lade(db) -> dict | None:
    """{url, token} der Konsolen-Instanz, oder None (VI für die Konsole aus)."""
    from app.services.settings import get_setting
    url = (await get_setting(db, URL_SCHLUESSEL) or "").strip()
    token = (await get_setting(db, TOKEN_SCHLUESSEL) or "").strip()
    if not url or not token:
        return None
    return {"url": url, "token": token}
