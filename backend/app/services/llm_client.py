from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LLMInvocationError(RuntimeError):
    pass


# ── Anthropic OAuth constants (must match Hermes anthropic_adapter) ───────────
# OAuth tokens (sk-ant-oat*) are only routed correctly when the request carries
# the Claude Code identity: Bearer auth, the oauth beta headers, a claude-cli
# user-agent and a system prompt that starts with the Claude Code prefix.
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_OAUTH_BETAS = "claude-code-20250219,oauth-2025-04-20"
_CLAUDE_CODE_UA = "claude-cli/2.1.74 (external, cli)"
_CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."


def _split_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split out system messages; return (system_prompt, non_system_messages).
    The Claude Code identity prefix is always prepended for OAuth routing."""
    system_parts: list[str] = [_CLAUDE_CODE_SYSTEM_PREFIX]
    convo: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))
        else:
            convo.append({"role": role, "content": content})
    return "\n\n".join(system_parts), convo


def _extract_anthropic_output(data: dict[str, Any]) -> str:
    blocks = data.get("content") or []
    parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(parts).strip()


def _build_api_url(base_url: str, path: str) -> str:
    normalized = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return f"{normalized}/{path.lstrip('/')}"


def _normalize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        normalized.append(
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
            }
        )
    return normalized


def _normalize_responses_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            role = "developer"
        normalized.append({"role": role, "content": message.get("content", "")})
    return normalized


def _build_codex_payload(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Build request body for chatgpt.com/backend-api/codex Responses API.

    The Codex endpoint uses a separate `instructions` field for the system prompt
    and an `input` array of typed message items. `store` must be False.
    """
    instructions = ""
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            instructions = content
            continue
        if role == "user":
            input_items.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": content}],
            })
        elif role == "assistant":
            input_items.append({
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            })

    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "store": False,
    }
    # NOTE: the Codex backend rejects `temperature` and `max_output_tokens`
    # with HTTP 400 "Unsupported parameter". They are accepted as kwargs for
    # call-site compatibility but deliberately NOT forwarded.
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def _extract_chat_output(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""

    content = (choices[0].get("message") or {}).get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        ]
        return "\n".join(part for part in text_parts if part).strip()
    return str(content).strip()


def _extract_responses_output(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content_item in item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts).strip()


async def _collect_codex_stream(response: Any) -> str:
    """Consume SSE stream from chatgpt.com/backend-api/codex and return full text."""
    import json as _json

    parts: list[str] = []
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw == "[DONE]":
            break
        try:
            event = _json.loads(raw)
        except Exception:
            continue
        etype = event.get("type", "")
        # delta events
        if etype in ("response.output_text.delta", "content_block_delta"):
            delta = event.get("delta") or event.get("text") or ""
            if isinstance(delta, str):
                parts.append(delta)
            elif isinstance(delta, dict):
                parts.append(delta.get("text") or "")
        # completed event — full text in output array
        elif etype == "response.completed":
            resp = event.get("response") or {}
            full = _extract_responses_output(resp)
            if full:
                return full
    return "".join(parts)


async def generate_text(
    llm_config: Any,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    mode = (getattr(llm_config, "api_mode", None) or "chat_completions").lower()
    headers = {"Content-Type": "application/json"}
    if getattr(llm_config, "api_key", None):
        headers["Authorization"] = f"Bearer {llm_config.api_key}"

    if mode == "anthropic_messages":
        # Claude via OAuth — Anthropic Messages API with Claude Code identity.
        system_prompt, convo = _split_anthropic_messages(messages)
        headers.update({
            "anthropic-version": _ANTHROPIC_VERSION,
            "anthropic-beta": _ANTHROPIC_OAUTH_BETAS,
            "user-agent": _CLAUDE_CODE_UA,
        })
        payload: dict[str, Any] = {
            "model": llm_config.model,
            "max_tokens": max_output_tokens or 1024,
            "system": system_prompt,
            "messages": convo,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        async with httpx.AsyncClient(timeout=llm_config.timeout_seconds, verify=False) as client:
            response = await client.post(_ANTHROPIC_MESSAGES_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            detail = response.text[:500].strip()
            msg = f"HTTP {response.status_code}: {detail}"
            log.warning("LLM call failed [anthropic_messages %s]: %s", llm_config.model, msg)
            raise LLMInvocationError(msg)
        return _extract_anthropic_output(response.json())

    if mode == "codex_responses":
        _codex_base = llm_config.base_url or "https://chatgpt.com/backend-api/codex"
        url = _build_api_url(_codex_base, "responses") + "?client_version=1.0.0"
        # Map thinking_mode → Codex reasoning effort. gpt-5.x defaults to HIGH
        # reasoning when no `reasoning` field is sent → slow even for trivial
        # prompts. We make it explicit so it is fast unless thinking is wanted:
        #   thinking on  → "high"  (e.g. generative dashboard)
        #   thinking off → "low"   (fast: diagnose / enrich / agent / chat)
        _valid = {"minimal", "low", "medium", "high"}
        _req = (reasoning_effort or "").lower()
        if _req in _valid:
            effort = _req
        else:  # None or "none"/unknown → derive from thinking_mode
            effort = "high" if getattr(llm_config, "thinking_mode", False) else "low"
        payload = _build_codex_payload(
            llm_config.model, messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            reasoning_effort=effort,
        )
        payload["stream"] = True
        async with httpx.AsyncClient(timeout=llm_config.timeout_seconds, verify=False) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    msg = f"HTTP {response.status_code}: {detail[:500].decode(errors='replace')}"
                    # Single chokepoint: log EVERY LLM failure at WARNING so a
                    # provider/param issue (like Codex rejecting temperature) is
                    # never hidden by a caller's silent fallback.
                    log.warning("LLM call failed [codex_responses %s]: %s", llm_config.model, msg)
                    raise LLMInvocationError(msg)
                return await _collect_codex_stream(response)
        return ""  # unreachable but satisfies type checkers

    elif mode == "responses":
        url = _build_api_url(llm_config.base_url, "responses")
        payload = {
            "model": llm_config.model,
            "input": _normalize_responses_messages(messages),
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        extract = _extract_responses_output
    else:
        url = _build_api_url(llm_config.base_url, "chat/completions")
        payload = {
            "model": llm_config.model,
            "messages": _normalize_chat_messages(messages),
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if getattr(llm_config, "thinking_mode", False):
            payload["enable_thinking"] = True
            payload["thinking_budget"] = getattr(llm_config, "thinking_budget", 1500)
        extract = _extract_chat_output

    async with httpx.AsyncClient(timeout=llm_config.timeout_seconds, verify=False) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code >= 400:
        detail = response.text[:500].strip()
        msg = f"HTTP {response.status_code}: {detail}"
        log.warning("LLM call failed [%s %s]: %s", mode, llm_config.model, msg)
        raise LLMInvocationError(msg)

    data = response.json()
    return extract(data)
