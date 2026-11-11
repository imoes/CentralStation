"""Konsole an die lokale VI anschließen (vi_konsole.py) — reine Teile."""
import json

from app.services import vi_konsole as vk


def test_hook_kommt_aus_dem_paket():
    # backend/Dockerfile packt /usr/share/vi-mcp/hook/vi_hook.py aus vi-mcp/pakete/*.deb aus — im Repo liegt keine Kopie.
    import pytest
    if not vk.HOOK_QUELLE.exists():
        pytest.skip("Hook nur im gebauten Image (aus dem vi-mcp-Paket)")
    assert vk.HOOK_QUELLE.read_text().startswith("#!/usr/bin/env python3\n\"\"\"vi_hook")


def test_hook_befehl_traegt_umgebung_und_pfade():
    b = vk.hook_befehl("/home/yolo", "http://vi-mcp:8765/mcp")
    assert b.startswith("env VI_MCP_URL=http://vi-mcp:8765/mcp ")
    assert "VI_MCP_TOKEN_FILE=/home/yolo/.config/vi-mcp/token-konsole" in b
    assert "VI_HOOK_STATE=/home/yolo/.cache/vi-mcp/hook" in b
    assert b.endswith("python3 -I /home/yolo/.config/vi-mcp/vi_hook.py")


def test_einstellungen_mischen_behaelt_fremdes_und_ersetzt_alte_vi_hooks():
    fremd = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /opt/anderer.py"}]}
    alt_vi = {"matcher": "*", "hooks": [{"type": "command", "command": "env X=1 python3 -I /alt/vi_hook.py"}]}
    alt = {"model": "x", "hooks": {"PreToolUse": [fremd, alt_vi], "PostToolUse": [alt_vi]}}
    neu = vk.mische_einstellungen(alt, "BEFEHL vi_hook.py")
    assert neu["model"] == "x"
    assert neu["hooks"]["PreToolUse"][0] == fremd                      # fremder Hook bleibt
    assert sum("vi_hook.py" in json.dumps(e) for e in neu["hooks"]["PreToolUse"]) == 1   # alter vi-Hook ersetzt
    assert set(neu["hooks"]) == set(vk.EREIGNISSE)
    assert "matcher" not in neu["hooks"]["SessionEnd"][0] and "timeout" not in neu["hooks"]["SessionEnd"][0]["hooks"][0]
    assert neu["hooks"]["PostToolUseFailure"][0]["matcher"] == "*"


def test_ohne_vi_werden_nur_die_vi_hooks_entfernt():
    fremd = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /opt/anderer.py"}]}
    alt = vk.mische_einstellungen({"hooks": {"PreToolUse": [fremd]}}, "BEFEHL vi_hook.py")
    aus = vk.mische_einstellungen(alt, None)
    assert aus == {"hooks": {"PreToolUse": [fremd]}}
    assert vk.mische_einstellungen(vk.mische_einstellungen({}, "B vi_hook.py"), None) == {}


def test_host_fuer_proxy_ausnahme():
    assert vk.host_von("http://vi-mcp:8765/mcp") == "vi-mcp"
    assert vk.host_von(None) == "vi-mcp"


def test_mcp_eintrag_fuer_hermes_und_codex():
    srv = vk.mcp_server({"url": "http://vi-mcp:8765/mcp", "token": "T"})
    assert srv == {"transport": "streamable-http", "url": "http://vi-mcp:8765/mcp", "headers": {"Authorization": "Bearer T"}}


def test_codex_konfiguration_traegt_kopfzeilen_der_zusatzserver():
    from app.services.userenv_manager import _codex_config_toml
    toml = _codex_config_toml({"vi": vk.mcp_server({"url": "http://vi-mcp:8765/mcp", "token": "T"})}, cs_user_id="")
    assert "[mcp_servers.vi]" in toml and 'url = "http://vi-mcp:8765/mcp"' in toml
    assert 'http_headers = { "Authorization" = "Bearer T" }' in toml.split("[mcp_servers.vi]")[1]


def test_briefing_nennt_die_vi_statt_store_knowledge():
    from app.services.userenv_manager import agent_instructions
    for kb in (True, False):
        t = agent_instructions("marvin", kb_available=kb)
        assert "vi_erinnere" in t and "vi_lerne" in t and "store_knowledge" not in t


def test_wissens_werkzeuge_sind_vom_mcp_server_entfernt():
    import pathlib
    quelle = pathlib.Path("app/api/mcp_server.py").read_text()
    for name in ("store_knowledge", "search_knowledge", "update_knowledge", "forget_knowledge"):
        assert f"async def {name}(" not in quelle, name


def test_jede_frage_fragt_die_vi():
    """Nutzer 11.11.2026: die VI sucht bei jeder Nutzerfrage, auch bei Folgefragen (vi-mcp ≥ 0.44.0, vi_prompt_context)."""
    neu = vk.mische_einstellungen({}, "python3 -I /h/vi_hook.py")
    e = neu["hooks"]["UserPromptSubmit"]
    assert len(e) == 1 and e[0]["matcher"] == "*" and e[0]["hooks"][0]["timeout"] == 5
