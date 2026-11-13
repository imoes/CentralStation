from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

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


#: Ein Versionssegment im Pfad: /v1, /v2, /v1beta, /router/v1, /api/v1 …
_VERSION_SEGMENT = re.compile(r"/v\d+[a-z0-9]*(?:/|$)", re.IGNORECASE)
#: OpenAI-kompatible APIs, deren Pfad bewusst KEIN Versionssegment hat: das
#: ChatGPT-Backend (Codex: …/backend-api/codex/responses) und Azure OpenAI
#: (…/openai/deployments/<name>/…?api-version=).
_UNVERSIONED_APIS = ("/backend-api/", "/openai/deployments/")


def openai_base_url(base_url: str) -> str:
    """Basis-URL einer OpenAI-kompatiblen API — immer mit Versionspfad.

    Eine Regel für alle, die daraus eine URL bauen (LLM-Aufrufe, Verbindungstest,
    Übergabe an Hermes). Fehlt das Versionssegment, wird /v1 ergänzt: eingetragen war
    seit 22.05.2026 "https://llm-host03.example.com/qwen35b", und jeder Aufruf ging an
    /qwen35b/chat/completions statt /qwen35b/v1/chat/completions. Das funktionierte
    nur, weil llama-server beide Routen bedient — andere Server (vLLM, LiteLLM …)
    tun das nicht, und in Logs sah es aus wie ein Fehlaufruf.
    Ein eingetragener Endpunkt (…/chat/completions, …/responses) wird abgeschnitten.
    """
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return normalized
    for suffix in ("/chat/completions", "/responses", "/models"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    path = urlparse(normalized).path + "/"
    if _VERSION_SEGMENT.search(path) or any(m in path for m in _UNVERSIONED_APIS):
        return normalized
    return f"{normalized}/v1"


def _build_api_url(base_url: str, path: str) -> str:
    return f"{openai_base_url(base_url)}/{path.lstrip('/')}"


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
    # NOTE: the Codex backend rejects `temperature` with HTTP 400 "Unsupported
    # parameter". It is accepted as a kwarg for call-site compatibility but
    # deliberately NOT forwarded.
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def _extract_chat_output(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        log.warning("_extract_chat_output: no choices in response — full data keys: %s", list(data.keys()))
        return ""

    msg = choices[0].get("message") or {}
    content = msg.get("content", "")
    reasoning = msg.get("reasoning_content", "")
    if not content and reasoning:
        # Qwen3 A3B on llama.cpp: enable_thinking=False is ignored (llama.cpp bug
        # #20182). The model always writes the full reply (thinking + answer) into
        # reasoning_content; content stays "". Transparently fall back so callers
        # can extract the answer from the thinking block via _parse_json.
        log.debug(
            "_extract_chat_output: content empty — using reasoning_content (len=%d)",
            len(reasoning),
        )
        return reasoning.strip()
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


#: Pflichtangabe für die Anthropic-Messages-API (siehe generate_text). Bewusst KEIN
#: Parameter von generate_text: ein Ausgabe-Limit setzt in CentralStation niemand —
#: das Modell begrenzt sich über seine Kontextgröße selbst (globale Regel).
ANTHROPIC_MAX_TOKENS = 32000

#: llama.cpp-spezifische Felder im chat_completions-Payload (siehe generate_text).
_THINKING_KEYS = frozenset({"enable_thinking", "thinking_budget", "chat_template_kwargs"})
#: Wortlaute strenger Anbieter, wenn sie Felder ablehnen. Gemessen 13.11.2026 an llmbase.ai (z-ai/glm-5.3-flash):
#: mit chat_template_kwargs {"enable_thinking": false} kam „does not support the requested features: tools,
#: max_tokens“ — kein Feldname im Text, obwohl die Anfrage weder max_tokens enthielt noch an tools scheiterte (ohne
#: das Feld lief derselbe Werkzeugaufruf). Mit true: „does not support chat_template_kwargs thinking controls“.
_ABLEHNUNG = ("does not support", "unsupported", "invalid request parameter", "unknown request parameter")
#: (base_url, Modell), die die llama.cpp-Felder ablehnen — danach ohne sie, statt jeden Schritt doppelt zu schicken.
_OHNE_THINKING: set[tuple[str, str]] = set()


def lehnt_thinking_felder_ab(status: int, text: str, payload: dict) -> bool:
    """400, weil der Anbieter die llama.cpp-Felder nicht kennt? Ein anderer 400 („model not found“) nicht. Rein."""
    if status != 400 or not (_THINKING_KEYS & payload.keys()):
        return False
    t = (text or "").lower()
    return any(k in t for k in _THINKING_KEYS) or any(w in t for w in _ABLEHNUNG)


async def _post_mit_rueckfall(client, url: str, headers: dict, payload: dict, llm_config: Any):
    """POST; lehnt der Anbieter die llama.cpp-Felder ab, einmal ohne sie — und für diesen Endpunkt merken."""
    schluessel = (str(getattr(llm_config, "base_url", "")), str(getattr(llm_config, "model", "")))
    if schluessel in _OHNE_THINKING:
        payload = {k: v for k, v in payload.items() if k not in _THINKING_KEYS}
    response = await client.post(url, headers=headers, json=payload)
    if lehnt_thinking_felder_ab(response.status_code, response.text, payload):
        log.info("LLM %s lehnt llama.cpp-Felder ab (%s) — erneut ohne sie, für diesen Endpunkt gemerkt",
                 llm_config.model, response.text[:160].strip())
        payload = {k: v for k, v in payload.items() if k not in _THINKING_KEYS}
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code < 400:
            _OHNE_THINKING.add(schluessel)
    return response


async def generate_text(
    llm_config: Any,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    json_prefill: str | None = None,
) -> str:
    """Call the configured LLM and return the answer text.

    json_prefill: when set (e.g. "```json\\n{"), the model is steered to emit JSON
    immediately instead of a long planning monologue. This is provider-aware:
      - chat_completions (llama.cpp/Qwen): sent as a trailing assistant turn; the
        server echoes the prefill so the returned content already carries it.
      - anthropic_messages (Claude): sent as an assistant prefill turn; Anthropic
        continues WITHOUT echoing it, so we prepend it back to the result.
      - codex_responses / responses: unsupported (the API generates a fresh turn),
        so the prefill is ignored — those providers already return clean JSON.
    """
    mode = (getattr(llm_config, "api_mode", None) or "chat_completions").lower()
    headers = {"Content-Type": "application/json"}
    if getattr(llm_config, "api_key", None):
        headers["Authorization"] = f"Bearer {llm_config.api_key}"

    if mode == "anthropic_messages":
        # Claude via OAuth — Anthropic Messages API with Claude Code identity.
        system_prompt, convo = _split_anthropic_messages(messages)
        if json_prefill:
            # Assistant prefill turn — Anthropic continues from it (must not end in
            # trailing whitespace, which "```json\n{" satisfies).
            convo = convo + [{"role": "assistant", "content": json_prefill}]
        headers.update({
            "anthropic-version": _ANTHROPIC_VERSION,
            "anthropic-beta": _ANTHROPIC_OAUTH_BETAS,
            "user-agent": _CLAUDE_CODE_UA,
        })
        payload: dict[str, Any] = {
            "model": llm_config.model,
            # Pflichtfeld der Messages-API — kein Limit im Sinne der Regel "nie ein
            # Token-Cap": der Wert liegt bei/über der Ausgabegrenze aller aktuellen
            # Claude-Modelle und schneidet daher nichts ab. Früher 1024 — das kappte
            # Antworten (finish_reason=length) und ist genau der verbotene Fall.
            "max_tokens": ANTHROPIC_MAX_TOKENS,
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
        out = _extract_anthropic_output(response.json())
        # Anthropic does not echo the prefill — prepend it so callers see full JSON.
        return (json_prefill + out) if json_prefill else out

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
        extract = _extract_responses_output
    else:
        url = _build_api_url(llm_config.base_url, "chat/completions")
        chat_messages = _normalize_chat_messages(messages)
        if json_prefill:
            # Trailing assistant turn — llama.cpp continues and echoes the prefill,
            # so the returned content already starts with it (no prepend needed).
            chat_messages = chat_messages + [{"role": "assistant", "content": json_prefill}]
        payload = {
            "model": llm_config.model,
            "messages": chat_messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        # Qwen3 models always think by default (reasoning_content) and only
        # write the answer to content AFTER finishing the thinking phase.
        # Always send both flags so the model knows exactly what we want:
        # - thinking_mode=True  → enable + full budget (slow, deep analysis)
        # - thinking_mode=False → enable + hard cap of 1024 tokens so the model
        #   stops thinking quickly and still has time to generate the JSON answer
        #   within the configured timeout. Without a cap the model may exhaust
        #   the timeout while thinking and leave content="".
        thinking_on = getattr(llm_config, "thinking_mode", False)
        # llama.cpp liest den Schalter NUR aus chat_template_kwargs; das Feld auf oberster
        # Ebene ignoriert es. Gemessen 13.10. (Ticket-Entwurf, qwen auf llm-host03): nur
        # oberste Ebene → Thinking lief, > 300 s, Timeout; mit chat_template_kwargs → 29 s,
        # 0 Zeichen Reasoning, gleiche Antwortqualität.
        payload["chat_template_kwargs"] = {"enable_thinking": thinking_on}
        payload["enable_thinking"] = thinking_on
        payload["thinking_budget"] = (
            getattr(llm_config, "thinking_budget", 1500) if thinking_on else 512
        )
        extract = _extract_chat_output

    async with httpx.AsyncClient(timeout=llm_config.timeout_seconds, verify=False) as client:
        # enable_thinking/thinking_budget/chat_template_kwargs sind llama.cpp-Erweiterungen. Strenge
        # OpenAI-kompatible Anbieter lehnen die GANZE Anfrage ab — dann einmal ohne sie, statt zu scheitern.
        response = await _post_mit_rueckfall(client, url, headers, payload, llm_config)

    if response.status_code >= 400:
        detail = response.text[:500].strip()
        msg = f"HTTP {response.status_code}: {detail}"
        log.warning("LLM call failed [%s %s]: %s", mode, llm_config.model, msg)
        raise LLMInvocationError(msg)

    data = response.json()
    out = extract(data)
    # Safety net: if a chat server did not echo the prefill, prepend it so the
    # caller always receives complete JSON (llama.cpp normally echoes it already).
    if json_prefill and mode not in ("responses",) and out and not out.lstrip().startswith(("```", "{")):
        out = json_prefill + out
    return out


# ── Werkzeugaufrufe (Tool-Calling) — für die KI-Schleife der Log-Überwachung ──

class WerkzeugeNichtUnterstuetzt(LLMInvocationError):
    """Der konfigurierte API-Modus kann (hier) keine Werkzeugaufrufe — benannt, nicht still."""


@dataclass
class Werkzeugantwort:
    """Antwort eines Schritts: entweder Werkzeugaufrufe oder Text (oder beides leer = benannt)."""
    text: str
    aufrufe: list[dict]             # [{"id", "name", "argumente": dict, "fehler": str|None}]
    #: Assistenten-Nachricht für den Verlauf (mit tool_calls), unverändert vom Server
    nachricht: dict


def werkzeug_aufrufe(msg: dict) -> list[dict]:
    """tool_calls einer Chat-Antwort → [{"id","name","argumente","fehler"}]. Ungültiges JSON
    in den Argumenten ist ein benannter Fehler je Aufruf, kein Abbruch der Schleife."""
    out = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        roh = fn.get("arguments")
        argumente, fehler = {}, None
        if isinstance(roh, dict):
            argumente = roh
        elif roh:
            try:
                argumente = json.loads(roh)
                if not isinstance(argumente, dict):
                    argumente, fehler = {}, "Argumente sind kein JSON-Objekt"
            except json.JSONDecodeError as e:
                fehler = f"Argumente kein gültiges JSON: {e.msg}"
        out.append({"id": tc.get("id") or f"aufruf_{i}", "name": fn.get("name") or "",
                    "argumente": argumente, "fehler": fehler})
    return out


async def generate_with_tools(llm_config: Any, messages: list[dict[str, Any]],
                              tools: list[dict[str, Any]], *,
                              temperature: float | None = None) -> Werkzeugantwort:
    """Ein Schritt mit Werkzeugen (OpenAI-Format `tools`). Kein max_tokens (globale Regel).

    `messages` darf Assistenten-Nachrichten mit `tool_calls` und `role: tool`-Antworten
    enthalten — sie werden unverändert weitergereicht.
    """
    mode = (getattr(llm_config, "api_mode", None) or "chat_completions").lower()
    if mode != "chat_completions":
        raise WerkzeugeNichtUnterstuetzt(
            f"Werkzeugaufrufe für API-Modus {mode} sind noch nicht gebaut (nur chat_completions)")
    headers = {"Content-Type": "application/json"}
    if getattr(llm_config, "api_key", None):
        headers["Authorization"] = f"Bearer {llm_config.api_key}"
    thinking_on = getattr(llm_config, "thinking_mode", False)
    payload: dict[str, Any] = {
        "model": llm_config.model, "messages": messages, "tools": tools, "tool_choice": "auto",
        "chat_template_kwargs": {"enable_thinking": thinking_on},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    url = _build_api_url(llm_config.base_url, "chat/completions")
    async with httpx.AsyncClient(timeout=llm_config.timeout_seconds, verify=False) as client:
        # 13.11.: llmbase.ai lehnte jeden Werkzeugaufruf mit chat_template_kwargs ab (41× in 10 min, KI-Analyse stand)
        response = await _post_mit_rueckfall(client, url, headers, payload, llm_config)
    if response.status_code >= 400:
        msg = f"HTTP {response.status_code}: {response.text[:500].strip()}"
        log.warning("LLM tool call failed [%s]: %s", llm_config.model, msg)
        raise LLMInvocationError(msg)
    msg = ((response.json().get("choices") or [{}])[0].get("message")) or {}
    nachricht = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        nachricht["tool_calls"] = msg["tool_calls"]
    return Werkzeugantwort(text=msg.get("content") or "", aufrufe=werkzeug_aufrufe(msg),
                           nachricht=nachricht)
