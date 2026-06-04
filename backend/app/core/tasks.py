"""Background-task helper with mandatory exception logging.

Bare ``asyncio.create_task(coro())`` swallows exceptions: if the coroutine
raises, the error is never retrieved and vanishes (only a late
"Task exception was never retrieved" on GC, often lost). This is exactly how
agent / enrichment / correlation failures became invisible.

``run_background`` wraps the coroutine so EVERY failure is logged at ERROR
with a traceback and the task name.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

log = logging.getLogger("app.core.tasks")


def run_background(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task:
    """Fire-and-forget a coroutine; log any exception at ERROR (never silent)."""
    async def _wrapped() -> None:
        try:
            await coro
        except Exception:
            log.error("Background task '%s' failed", name, exc_info=True)

    return asyncio.create_task(_wrapped(), name=name)
