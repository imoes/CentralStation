"""Konsole an die lokale VI anschließen (vi_konsole.py) — reine Teile."""
import hashlib
import json

from app.services import vi_konsole as vk


def test_hook_ist_das_unveraenderte_paketskript():
    # /usr/share/vi-mcp/hook/vi_hook.py aus vi-mcp_0.14.3-1_all.deb (Release v0.14.3)
    roh = vk.HOOK_QUELLE.read_bytes()
    assert hashlib.sha256(roh).hexdigest() == "44ca2e1bebc8937d5dcd2eab1af3273b2ffad58445401261e81dd8662479658a"


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
