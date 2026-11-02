"""Playwright-MCP im Konsolen-Container braucht den Proxy ausdrücklich (02.11.2026:
ERR_TIMED_OUT für jede Internetseite, weil Chromium HTTP(S)_PROXY nicht übernimmt)."""
from __future__ import annotations

from app.services import userenv_manager as um


def test_proxy_and_bypass_are_passed_to_the_browser(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:80")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,.example.com")
    args = um.playwright_mcp_args()
    assert args[args.index("--proxy-server") + 1] == "http://proxy.example:80"
    bypass = args[args.index("--proxy-bypass") + 1].split(",")
    assert ".example.com" in bypass and "localhost" in bypass
    assert "backend" in bypass                       # Backend über das Docker-Netz, nie über den Proxy
    assert bypass == um.no_proxy_entries()           # dieselbe Liste wie NO_PROXY im Container
    assert args[:len(um._PLAYWRIGHT_MCP_ARGS)] == um._PLAYWRIGHT_MCP_ARGS


def test_no_proxy_configured_means_no_proxy_args(monkeypatch):
    for v in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(v, raising=False)
    assert "--proxy-server" not in um.playwright_mcp_args()
