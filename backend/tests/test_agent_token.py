"""Konsolen-Agenten weisen sich mit einem HMAC-Token aus, nicht nur mit ihrer Nutzer-ID.

Bis 2026-10-11 genügte X-CS-User-ID: jeder, der /api erreichte, konnte sich beim
MCP-Server als beliebiger Nutzer ausgeben und über /computer/agent-credentials die
OAuth-Tokens fremder CLI-Agenten abholen.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.security import agent_token, verify_agent_token

U1 = str(uuid.uuid4())
U2 = str(uuid.uuid4())


def test_token_is_bound_to_the_user():
    assert verify_agent_token(U1, agent_token(U1))
    assert not verify_agent_token(U1, agent_token(U2))   # fremdes Token
    assert not verify_agent_token(U1, "")
    assert not verify_agent_token(U1, None)
    assert not verify_agent_token(None, agent_token(U1))


def test_token_is_stable_and_not_the_user_id():
    assert agent_token(U1) == agent_token(U1)
    assert U1 not in agent_token(U1)


def _headers(monkeypatch, headers: dict):
    import fastmcp.server.dependencies as deps
    monkeypatch.setattr(deps, "get_http_headers", lambda include_all=True: headers)


def test_mcp_caller_without_token_is_unidentified(monkeypatch):
    from app.api.mcp_server import _calling_user_id
    _headers(monkeypatch, {"x-cs-user-id": U1})
    assert _calling_user_id() is None


def test_mcp_caller_with_foreign_token_is_unidentified(monkeypatch):
    from app.api.mcp_server import _calling_user_id
    _headers(monkeypatch, {"x-cs-user-id": U1, "x-cs-agent-token": agent_token(U2)})
    assert _calling_user_id() is None


def test_mcp_caller_with_valid_token_is_identified(monkeypatch):
    from app.api.mcp_server import _calling_user_id
    _headers(monkeypatch, {"x-cs-user-id": U1, "x-cs-agent-token": agent_token(U1)})
    assert _calling_user_id() == U1


def test_agent_headers_and_codex_config_carry_the_token():
    from app.services.userenv_manager import _agent_headers, _codex_config_toml
    assert _agent_headers(U1) == {"X-CS-User-ID": U1, "X-CS-Agent-Token": agent_token(U1)}
    toml = _codex_config_toml({}, cs_user_id=U1)
    assert f'"X-CS-Agent-Token" = "{agent_token(U1)}"' in toml


def test_agent_credentials_endpoint_checks_the_token():
    """Der Endpunkt ist unter conftest nicht importierbar (Modell-Stubs); geprüft wird,
    dass er dieselbe Prüfung nutzt wie der MCP-Server — eine Regelstelle."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("app/api/computer_proxy.py").read_text()
    block = src[src.index("async def get_agent_credentials"):]
    block = block[:block.index("\n@router")]
    assert "verify_agent_token(user_id, request.headers.get(\"X-CS-Agent-Token\"))" in block
    assert "HTTPException(403" in block
