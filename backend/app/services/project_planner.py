"""Agentic project planner — lets the LLM use web_search + web_fetch tools
before producing the final plan.

The planner is provider-agnostic: instead of relying on native tool-calling
APIs, the LLM emits a JSON `{"action":"tools", ...}` request, the backend
executes the tools and feeds the observations back as a `<tool_results>`
message, and loops until the LLM emits `{"action":"plan", ...}`.
"""
from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any

import httpx

from app.services.llm_client import generate_text

log = logging.getLogger(__name__)

MAX_ROUNDS = 4              # research rounds before forcing a plan
MAX_TOOL_CALLS_PER_ROUND = 4
FETCH_MAX_CHARS = 6000     # truncate fetched page text
SEARCH_RESULTS = 5


# ── Tool implementations ───────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Strip HTML to readable text, skipping script/style/nav noise."""

    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._parts))


async def _web_search(query: str, searxng_url: str) -> str:
    if not searxng_url:
        return "(Websuche nicht konfiguriert)"
    try:
        async with httpx.AsyncClient(timeout=12.0, verify=False) as client:
            r = await client.get(
                f"{searxng_url}/search",
                params={"q": query, "format": "json", "categories": "general,it"},
            )
            if r.status_code == 200:
                results = r.json().get("results", [])[:SEARCH_RESULTS]
                if not results:
                    return "(keine Treffer)"
                return "\n".join(
                    f"- {x.get('title')} [{x.get('url')}]: {x.get('content', '')[:240]}"
                    for x in results
                )
            return f"(Suche fehlgeschlagen: HTTP {r.status_code})"
    except Exception as e:
        log.debug("planner web_search failed: %s", e)
        return f"(Suche fehlgeschlagen: {e})"


async def _web_fetch(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "(ungueltige URL)"
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "CentralStation-Planner/1.0"})
        if r.status_code >= 400:
            return f"(Abruf fehlgeschlagen: HTTP {r.status_code})"
        ctype = r.headers.get("content-type", "")
        body = r.text
        if "html" in ctype:
            parser = _TextExtractor()
            parser.feed(body)
            body = parser.text()
        return body[:FETCH_MAX_CHARS]
    except Exception as e:
        log.debug("planner web_fetch failed for %s: %s", url, e)
        return f"(Abruf fehlgeschlagen: {e})"


# ── Agentic loop ────────────────────────────────────────────────────────────────

def _strip_json(raw: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```$", "", text)
    return text


def _parse(raw: str) -> dict | None:
    text = _strip_json(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                return None
    return None


async def run_planner_agent(
    llm_config: Any,
    messages: list[dict[str, str]],
    searxng_url: str,
) -> dict:
    """Run the research+plan loop. Returns a dict with keys:
    reply, steps, open_points, sources, tool_activity, raw (fallback text).
    """
    convo = list(messages)
    tool_activity: list[dict] = []

    for round_idx in range(MAX_ROUNDS):
        # On the last allowed round, force a plan (no more tool use).
        force_plan = round_idx == MAX_ROUNDS - 1
        if force_plan:
            convo.append({
                "role": "user",
                "content": "<system_note>Beende die Recherche und liefere jetzt den finalen Plan (action=plan).</system_note>",
            })

        raw = await generate_text(llm_config, convo, max_output_tokens=4096, reasoning_effort="low")
        data = _parse(raw)

        if data is None:
            # Unparseable — return as plain reply, no steps.
            return {"reply": raw, "steps": [], "open_points": [], "sources": [],
                    "tool_activity": tool_activity}

        action = data.get("action")

        if action == "tools" and not force_plan:
            calls = data.get("tool_calls", [])[:MAX_TOOL_CALLS_PER_ROUND]
            observations: list[str] = []
            for call in calls:
                tool = call.get("tool")
                if tool == "web_search":
                    q = call.get("query", "")
                    res = await _web_search(q, searxng_url)
                    tool_activity.append({"tool": "web_search", "detail": q,
                                          "ok": not res.startswith("(")})
                    observations.append(f"[web_search] {q}\n{res}")
                elif tool == "web_fetch":
                    url = call.get("url", "")
                    res = await _web_fetch(url)
                    tool_activity.append({"tool": "web_fetch", "detail": url,
                                          "ok": not res.startswith("(")})
                    observations.append(f"[web_fetch] {url}\n{res}")
            # Record the assistant's tool request + feed back observations.
            convo.append({"role": "assistant", "content": raw})
            convo.append({
                "role": "user",
                "content": "<tool_results>\n" + "\n\n".join(observations) + "\n</tool_results>",
            })
            continue

        # action == "plan" (or anything with steps) → done.
        return {
            "reply": data.get("reply", ""),
            "steps": data.get("steps", []),
            "open_points": data.get("open_points", []) or [],
            "sources": data.get("sources", []) or [],
            "tool_activity": tool_activity,
        }

    # Loop exhausted without a plan.
    return {"reply": "Konnte keinen Plan erzeugen.", "steps": [], "open_points": [],
            "sources": [], "tool_activity": tool_activity}
