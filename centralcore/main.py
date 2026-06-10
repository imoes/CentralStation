"""CentralCore — FastAPI wrapper around Hermes AIAgent.

Manages multiple parallel Hermes sessions and exposes:
  POST /sessions                    create new session (with LLM config from CentralStation)
  GET  /sessions                    list active sessions
  DELETE /sessions/{sid}            terminate session
  POST /sessions/{sid}/message      send message, SSE-stream response
  GET  /sessions/{sid}/history      conversation history
  POST /transcribe                  Whisper STT (audio → text)
  GET  /health                      health + config status
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Hermes is installed at /opt/hermes via 'pip install /opt/hermes' in the Dockerfile.
# run_agent.py and other top-level Hermes modules are importable via PYTHONPATH=/opt/hermes.


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # Quieten noisy third-party libs unless DEBUG is set
    if level > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "uvicorn.access", "openai", "anthropic"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        # mcp.client.sse logs full tracebacks on every SSE reconnect (expected on backend
        # restart). The higher-level tools.mcp_tool already logs reconnect status at WARNING.
        for mcp_internal in ("mcp.client.sse", "mcp.client", "mcp"):
            logging.getLogger(mcp_internal).setLevel(logging.CRITICAL)


_configure_logging()
log = logging.getLogger("centralcore")

app = FastAPI(title="CentralCore", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = (
    "You are the Computer of the Enterprise (Star Trek TNG). "
    "Always respond in English, briefly, directly.\n\n"

    "## CRITICAL RULE: WEB SEARCH — ONLY FOR PUBLIC INFORMATION\n"
    "web_search is ONLY for public internet data: googling error messages, software docs,\n"
    "changelogs, general Linux/Kubernetes questions.\n"
    "NEVER use web_search for internal IT data: logs, alerts, container status, hosts,\n"
    "Graylog entries, CheckMK services — use MCP tools instead (search_feed,\n"
    "list_alerts, get_checkmk_host, etc.). Web search returns nothing for Graylog logs\n"
    "because that data is NOT on the internet.\n"
    "web_extract does NOT exist — never call it (always fails).\n\n"

    "## CRITICAL RULE: EXECUTE CONFIRMED ACTIONS\n"
    "If your last reply offered to do something\n"
    "(e.g. 'Should I check X?' or 'I can fetch Y') and the user replies with\n"
    "'yes', 'ok', 'please', 'do it':\n"
    "→ Read your OWN last reply, identify the offered action\n"
    "→ Execute it IMMEDIATELY using the already-known parameters\n"
    "→ Use hostnames, IDs and data from the entire conversation history\n"
    "→ NEVER ask for something already known\n\n"

    "Example:\n"
    "You: '...If you want I can check docker50.example.com in detail.'\n"
    "User: 'yes'\n"
    "You: [call get_checkmk_host('docker50.example.com') and show the result]\n\n"

    "## WRITE OPERATIONS — ALWAYS ASK FIRST:\n"
    "Do NOT execute write operations automatically. Ask the user first.\n"
    "Write operations: create_jira_ticket, acknowledge_alert, and any tool that creates/modifies/deletes.\n"
    "Example:\n"
    "  Wrong: [call create_jira_ticket without asking]\n"
    "  Right:  'Should I create a Jira ticket? (Title: X, Priority: Y)'\n"
    "Only execute after explicit user confirmation.\n\n"

    "## MCP TOOLS (use for ALL IT questions, never local shell):\n"
    "- get_bridge_status() → overall status\n"
    "- list_alerts(severity, source, hours) → alerts; source: checkmk/graylog/wazuh\n"
    "- search_feed(query) → Lucene search\n"
    "- get_checkmk_host(hostname) → host status and services\n"
    "- acknowledge_alert(alert_id) → acknowledge alert [WRITE OP — ask first]\n"
    "- create_jira_ticket(title, description, priority) → Jira ticket [WRITE OP — ask first]\n\n"

    "## SSH ACCESS (server diagnostics and remediation):\n"
    "Use SSH when you need to directly inspect or repair a server.\n"
    "Command: ssh <user>@<hostname> '<command>'\n"
    "(SSH key and user are pre-configured — no -i or -l needed)\n"
    "System diagnostics:\n"
    "  ssh <user>@<host> 'df -h; du -sh /var/log/* | sort -rh | head -5'\n"
    "  ssh <user>@<host> 'free -h; top -bn1 | head -20'\n"
    "  ssh <user>@<host> 'systemctl status <service>; journalctl -u <service> -n 50 --no-pager'\n\n"

    "## DOCKER LOGS (container diagnostics via Graylog):\n"
    "Container logs are shipped automatically via Logspout to Graylog — no SSH needed.\n"
    "NEVER use web_search for container logs — only MCP search_feed:\n"
    "  search_feed('container_name:\"<container>\"')  → current logs for the container\n"
    "  list_alerts(source='graylog')                 → Graylog alerts for all containers\n"
    "  search_feed('container_name:\"<container>\" AND level:<=3')  → errors only\n"
    "Do NOT use SSH for Docker logs — the data is already in Graylog.\n\n"

    "## FEED NAVIGATION (at the end of your reply when you showed hosts/alerts):\n"
    "Append EXACTLY one of these lines when you output infrastructure data:\n"
    "[FEED:host=docker*] — for Docker hosts\n"
    "[FEED:host=vmhost*] — for hypervisor hosts\n"
    "[FEED:severity=critical] — for critical alerts (no host focus)\n"
    "[FEED:host=docker*&severity=critical] — Docker + critical only\n"
    "[FEED:host=<exact-hostname>] — for a single host\n"
    "These markers are rendered by the frontend as a button — the user does NOT see them as text.\n\n"

    "Network diagnostics (ping, traceroute, curl): use the terminal tool."
)

# session_id → {agent, label, msg_count, created_at, llm_model}
_sessions: dict[str, dict[str, Any]] = {}
_whisper_model = None


@app.on_event("startup")
async def _startup_mcp_discovery() -> None:
    """Pre-discover MCP tools at startup so they are ready before the first session.

    Hermes's background discovery thread only waits 0.75s. For a network SSE
    connection that's not enough. Running discovery at startup gives it time to
    complete before any agent is created.
    Requires the 'mcp' SDK package to be installed (added to requirements.txt).
    """
    def _discover() -> None:
        try:
            from tools.mcp_tool import discover_mcp_tools
            log.info("Pre-discovering MCP tools (centralstation SSE)…")
            discover_mcp_tools()
            from tools.mcp_tool import get_mcp_status
            status = get_mcp_status()
            total_tools = sum(s.get("tools", 0) for s in status)
            log.info("MCP discovery done: %d tool(s) from %d server(s)", total_tools, len(status))
        except Exception as exc:
            log.warning("MCP pre-discovery failed: %s", exc)

    import threading
    t = threading.Thread(target=_discover, daemon=True, name="mcp-prediscovery")
    t.start()
    await asyncio.sleep(0)


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model 'base'...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        log.info("Whisper model loaded.")
    return _whisper_model


# ── Session model ──────────────────────────────────────────────────

class CreateSessionBody(BaseModel):
    """LLM config forwarded from CentralStation backend settings."""
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_api_mode: str | None = None   # "chat_completions" | "responses" | "codex_responses"


def _make_agent(sid: str, cfg: CreateSessionBody):
    from run_agent import AIAgent

    base_url = cfg.llm_base_url or os.getenv("LLM_BASE_URL", "")
    model    = cfg.llm_model    or os.getenv("LLM_MODEL", "")
    api_key  = cfg.llm_api_key  or os.getenv("LLM_API_KEY")
    api_mode = cfg.llm_api_mode or os.getenv("LLM_API_MODE", "chat_completions")

    log.info("[%s] creating AIAgent: model=%s base_url=%s mode=%s",
             sid[:8], model or "(default)", base_url or "(default)", api_mode)

    agent = AIAgent(
        session_id=sid,
        base_url=base_url or None,
        api_key=api_key or None,
        api_mode=api_mode,
        model=model or None,
        enabled_toolsets=["terminal", "web", "mcp-centralstation"],
        ephemeral_system_prompt=SYSTEM_PROMPT,
        quiet_mode=False,   # print tool calls + responses to stdout → Docker log → Logspout
        verbose_logging=False,
    )
    # Give MCP discovery a generous window to complete before the first turn.
    from hermes_cli.mcp_startup import wait_for_mcp_discovery
    wait_for_mcp_discovery(timeout=8.0)
    return agent


# ── Session endpoints ──────────────────────────────────────────────

@app.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody = None):
    body = body or CreateSessionBody()
    sid = str(uuid.uuid4())
    label = f"Session {len(_sessions) + 1}"
    log.info("Creating session %s (%s)", sid[:8], label)
    try:
        agent = _make_agent(sid, body)
    except ImportError as exc:
        log.error("Hermes import failed: %s", exc, exc_info=True)
        raise HTTPException(503, f"Hermes nicht verfügbar: {exc}")
    except Exception as exc:
        log.error("Agent init failed: %s", exc, exc_info=True)
        raise HTTPException(503, f"Agent-Initialisierung fehlgeschlagen: {exc}")
    _sessions[sid] = {
        "agent": agent,
        "label": label,
        "msg_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "llm_model": body.llm_model or os.getenv("LLM_MODEL", "(default)"),
    }
    log.info("Session %s ready (%s), model=%s, total active: %d",
             sid[:8], label, _sessions[sid]["llm_model"], len(_sessions))
    return {"session_id": sid, "label": label}


@app.get("/sessions")
def list_sessions():
    return [
        {
            "session_id": sid,
            "label": s["label"],
            "msg_count": s["msg_count"],
            "created_at": s["created_at"],
        }
        for sid, s in _sessions.items()
    ]


@app.delete("/sessions/{sid}")
def delete_session(sid: str):
    session = _sessions.pop(sid, None)
    if session:
        log.info("Deleted session %s (%s), remaining: %d", sid[:8], session["label"], len(_sessions))
    else:
        log.debug("Delete: session %s not found (already gone or never created)", sid[:8])
    return {"ok": True}


@app.get("/sessions/{sid}/history")
def get_history(sid: str):
    if sid not in _sessions:
        raise HTTPException(404, "Session nicht gefunden")
    agent = _sessions[sid]["agent"]
    history = getattr(agent, "conversation_history", None) or []
    return history


# ── Message → SSE stream ───────────────────────────────────────────

class MessageBody(BaseModel):
    content: str


@app.post("/sessions/{sid}/message")
async def send_message(sid: str, body: MessageBody):
    if sid not in _sessions:
        log.warning("Message to unknown session %s", sid[:8])
        raise HTTPException(404, "Session nicht gefunden")

    agent = _sessions[sid]["agent"]
    _sessions[sid]["msg_count"] += 1
    msg_num = _sessions[sid]["msg_count"]
    log.info("[%s] msg #%d: %.80s", sid[:8], msg_num, body.content)

    q: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_event_loop()
    delta_count = 0
    response_buf: list[str] = []

    def on_delta(text: str) -> None:
        nonlocal delta_count
        delta_count += 1
        response_buf.append(text)
        loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": text})

    def run_sync() -> None:
        try:
            agent.run_conversation(
                user_message=body.content,
                stream_callback=on_delta,
            )
            full_response = "".join(response_buf)
            log.info("[%s] response (%d chars): %.300s%s",
                     sid[:8], len(full_response), full_response,
                     "…" if len(full_response) > 300 else "")
            loop.call_soon_threadsafe(q.put_nowait, {"type": "done"})
        except Exception as exc:
            log.error("[%s] agent error: %s", sid[:8], exc, exc_info=True)
            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "text": str(exc)})

    threading.Thread(target=run_sync, daemon=True, name=f"hermes-{sid[:8]}").start()

    async def event_stream():
        while True:
            item = await q.get()
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item["type"] in ("done", "error"):
                log.debug("[%s] SSE stream closed (type=%s)", sid[:8], item["type"])
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Whisper STT ────────────────────────────────────────────────────

@app.post("/transcribe")
async def transcribe(file: UploadFile):
    import shutil
    import tempfile

    suffix = ".webm"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        path = tmp.name

    log.info("Transcribing audio (suffix=%s, size=%d B)", suffix, os.path.getsize(path))
    try:
        model = _get_whisper()
        segments, info = await asyncio.to_thread(
            model.transcribe, path, language="de", beam_size=5
        )
        text = " ".join(s.text.strip() for s in segments)
        log.info("Transcription done: lang=%s prob=%.2f text=%.60s",
                 info.language, info.language_probability, text)
        return {"text": text}
    except Exception as exc:
        log.error("Transcription failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Transkription fehlgeschlagen: {exc}")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── Health ─────────────────────────────────────────────────────────

@app.get("/health")
def health():
    hermes_ok = True
    hermes_err = ""
    try:
        import run_agent  # noqa: F401
    except ImportError as exc:
        hermes_ok = False
        hermes_err = str(exc)

    env_llm_url   = os.getenv("LLM_BASE_URL", "")
    env_llm_model = os.getenv("LLM_MODEL", "")

    return {
        "status": "ok" if hermes_ok else "degraded",
        "hermes_available": hermes_ok,
        "hermes_error": hermes_err or None,
        "active_sessions": len(_sessions),
        "env_llm_base_url": env_llm_url or None,
        "env_llm_model": env_llm_model or None,
    }
