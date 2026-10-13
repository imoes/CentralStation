"""chat_completions gegen einen strengen OpenAI-kompatiblen Anbieter.

generate_text schickt llama.cpp-Felder (enable_thinking, thinking_budget) mit. Ein
Anbieter wie llmbase.ai lehnt daraufhin die ganze Anfrage ab — der Hermes-LLM-Test
zeigte rot, obwohl Hermes selbst (das die Felder nicht sendet) einwandfrei lief.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import llm_client


def _cfg() -> SimpleNamespace:
    # generate_text liest die Konfiguration nur über Attribute; LLMConfig selbst
    # zöge app.core.config mit, das ohne .env nicht lädt.
    return SimpleNamespace(base_url="https://strict.example/v1", model="m", api_key="k",
                           timeout_seconds=5, api_mode="chat_completions",
                           thinking_mode=False)


def _patch_transport(monkeypatch, handler) -> list[dict]:
    seen: list[dict] = []
    real = httpx.AsyncClient

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return handler(request)

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real(*args, transport=httpx.MockTransport(wrapped), **kwargs)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", factory)
    return seen


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})


def test_retries_without_thinking_params_when_provider_rejects_them(monkeypatch):
    def handler(request):
        if "enable_thinking" in json.loads(request.content):
            return httpx.Response(400, json={"error": {
                "message": "Unknown request parameter: enable_thinking"}})
        return _ok()

    seen = _patch_transport(monkeypatch, handler)
    out = asyncio.run(llm_client.generate_text(_cfg(), [{"role": "user", "content": "x"}]))
    assert out == "OK"
    assert len(seen) == 2
    assert "enable_thinking" in seen[0]
    assert not llm_client._THINKING_KEYS & seen[1].keys()


def test_llama_cpp_keeps_the_thinking_params(monkeypatch):
    """Nimmt der Server die Felder an, bleibt es bei einer Anfrage mit ihnen."""
    seen = _patch_transport(monkeypatch, lambda r: _ok())
    asyncio.run(llm_client.generate_text(_cfg(), [{"role": "user", "content": "x"}]))
    assert len(seen) == 1
    assert "enable_thinking" in seen[0]


def test_other_400_is_not_retried(monkeypatch):
    seen = _patch_transport(monkeypatch, lambda r: httpx.Response(
        400, json={"error": {"message": "model not found"}}))
    with pytest.raises(llm_client.LLMInvocationError):
        asyncio.run(llm_client.generate_text(_cfg(), [{"role": "user", "content": "x"}]))
    assert len(seen) == 1


def test_thinking_switch_goes_where_llama_cpp_reads_it(monkeypatch):
    """llama.cpp liest enable_thinking NUR aus chat_template_kwargs (13.10.: oberste Ebene
    allein → Thinking lief, Ticket-Entwurf > 300 s; mit kwargs 29 s)."""
    seen = _patch_transport(monkeypatch, lambda r: _ok())
    asyncio.run(llm_client.generate_text(_cfg(), [{"role": "user", "content": "x"}]))
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "max_tokens" not in seen[0] and "n_predict" not in seen[0]      # globale Regel


def test_strict_provider_retry_also_drops_chat_template_kwargs(monkeypatch):
    def handler(request):
        if "chat_template_kwargs" in json.loads(request.content):
            return httpx.Response(400, json={"error": {
                "message": "Unknown request parameter: chat_template_kwargs"}})
        return _ok()
    seen = _patch_transport(monkeypatch, handler)
    assert asyncio.run(llm_client.generate_text(_cfg(), [{"role": "user", "content": "x"}])) == "OK"
    assert len(seen) == 2 and "chat_template_kwargs" not in seen[1]
