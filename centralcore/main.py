"""CentralCore — FastAPI wrapper around Hermes AIAgent.

Manages multiple parallel Hermes sessions and exposes:
  POST /sessions                    create new session
  GET  /sessions                    list active sessions
  DELETE /sessions/{sid}            terminate session
  POST /sessions/{sid}/message      send message, SSE-stream response
  GET  /sessions/{sid}/history      conversation history
  POST /transcribe                  Whisper STT (audio → text)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Hermes is volume-mounted at /hermes
sys.path.insert(0, os.environ.get("HERMES_PATH", "/hermes"))

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
        for noisy in ("httpx", "httpcore", "uvicorn.access"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


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
    "Du bist der Computer der Enterprise (Star Trek TNG). "
    "Du antwortest kurz, präzise und immer auf Deutsch. "
    "Du hast Zugriff auf das CentralStation IT-Monitoring-System via MCP-Tools. "
    "Nutze ping, curl und traceroute für Netzwerkdiagnosen direkt über das Terminal-Tool. "
    "Wenn du Aktionen ausführst, bestätige sie kurz und nenne das Ergebnis."
)

# session_id → {agent, label, msg_count, created_at}
_sessions: dict[str, dict[str, Any]] = {}
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model 'base'...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        log.info("Whisper model loaded.")
    return _whisper_model


def _make_agent(sid: str):
    from run_agent import AIAgent
    return AIAgent(
        session_id=sid,
        enabled_toolsets=["terminal", "web"],
        ephemeral_system_prompt=SYSTEM_PROMPT,
        persist_session=True,
        quiet_mode=True,
        verbose_logging=False,
    )


# ── Session endpoints ──────────────────────────────────────────────

@app.post("/sessions", status_code=201)
def create_session():
    sid = str(uuid.uuid4())
    label = f"Session {len(_sessions) + 1}"
    log.info("Creating session %s (%s)", sid[:8], label)
    try:
        agent = _make_agent(sid)
    except ImportError as exc:
        log.error("Hermes import failed: %s", exc)
        raise HTTPException(503, f"Hermes nicht verfügbar: {exc}")
    _sessions[sid] = {
        "agent": agent,
        "label": label,
        "msg_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("Session %s ready (%s), total active: %d", sid[:8], label, len(_sessions))
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
    if sid not in _sessions:
        log.warning("Delete: session %s not found", sid[:8])
        raise HTTPException(404, "Session nicht gefunden")
    label = _sessions[sid]["label"]
    del _sessions[sid]
    log.info("Deleted session %s (%s), remaining: %d", sid[:8], label, len(_sessions))
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

    def on_delta(text: str) -> None:
        nonlocal delta_count
        delta_count += 1
        if delta_count <= 3 or delta_count % 20 == 0:
            log.debug("[%s] delta #%d: %.40s", sid[:8], delta_count, text.replace("\n", "↵"))
        loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": text})

    def run_sync() -> None:
        log.debug("[%s] hermes thread started", sid[:8])
        try:
            agent.run_conversation(
                user_message=body.content,
                stream_callback=on_delta,
            )
            log.info("[%s] hermes done, %d deltas sent", sid[:8], delta_count)
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

    log.info("Transcribing audio file (suffix=%s, size=%d B)", suffix, os.path.getsize(path))
    try:
        model = _get_whisper()
        segments, info = await asyncio.to_thread(
            model.transcribe, path, language="de", beam_size=5
        )
        text = " ".join(s.text.strip() for s in segments)
        log.info("Transcription done: lang=%s, prob=%.2f, result=%.60s",
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
    try:
        import run_agent  # noqa: F401
    except ImportError:
        hermes_ok = False
    return {
        "status": "ok" if hermes_ok else "degraded",
        "hermes_available": hermes_ok,
        "active_sessions": len(_sessions),
    }
