from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LLMInvocationError(RuntimeError):
    pass


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

    if mode == "responses":
        url = _build_api_url(llm_config.base_url, "responses")
        payload: dict[str, Any] = {
            "model": llm_config.model,
            "input": _normalize_responses_messages(messages),
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
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

    async with httpx.AsyncClient(timeout=llm_config.timeout_seconds, verify=False) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code >= 400:
        detail = response.text[:500].strip()
        raise LLMInvocationError(f"HTTP {response.status_code}: {detail}")

    data = response.json()
    return _extract_responses_output(data) if mode == "responses" else _extract_chat_output(data)


async def generate_text_with_fallback(
    llm_config: Any,
    messages: list[dict[str, Any]],
    *,
    db: Any = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """generate_text() with automatic Codex fallback on failure.

    If the primary LLM fails (connection error, timeout, 5xx) AND
    OpenAI Codex fallback is enabled in settings AND a Hermes OAuth
    token is available, retries with the Codex config automatically.
    """
    try:
        return await generate_text(
            llm_config, messages,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
    except (LLMInvocationError, httpx.HTTPError, Exception) as primary_err:
        if db is None:
            raise

        # Try Codex fallback
        try:
            from app.services.settings import get_codex_config
            codex_cfg = await get_codex_config(db)
            if not codex_cfg:
                raise  # fallback not configured, re-raise original error

            log.warning(
                "Primary LLM failed (%s), falling back to Codex (%s/%s)",
                primary_err, codex_cfg.base_url, codex_cfg.model
            )
            result = await generate_text(
                codex_cfg, messages,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                # No reasoning_effort for Codex — it uses standard OpenAI params
            )
            log.info("Codex fallback succeeded")
            return result
        except Exception as fallback_err:
            log.warning("Codex fallback also failed: %s", fallback_err)
            raise primary_err from None
