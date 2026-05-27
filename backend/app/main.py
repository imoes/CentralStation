from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api import auth, users, connectors, alerts, kanban, network, ai, ws, audit, preferences, jira_view, workflow, feed, feed_searches, dashboard_widgets, help as help_router
from app.api import settings as settings_router
from app.core.config import settings
from app.core.opensearch import close_opensearch
from app.core.rate_limit import limiter
from app.core.redis import close_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.ai_agent.scheduler import start_scheduler, stop_scheduler
    from app.services.feed_index import ensure_indices

    await start_scheduler()
    # Ensure OpenSearch feed indices exist, then backfill existing DB alerts
    try:
        await ensure_indices()
        from app.services.feed_index import backfill_from_db
        await backfill_from_db(days=7)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("OpenSearch index setup deferred: %s", exc)
    yield
    stop_scheduler()
    await close_redis()
    await close_opensearch()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(connectors.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(kanban.router, prefix="/api")
app.include_router(network.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(jira_view.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(feed.router, prefix="/api")
app.include_router(feed_searches.router, prefix="/api")
app.include_router(dashboard_widgets.router, prefix="/api")
app.include_router(help_router.router, prefix="/api")
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/health/detailed")
async def health_detailed():
    """Detailed health check for all critical services."""
    import asyncio

    checks: dict[str, str] = {}

    # Database check
    try:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis check
    try:
        from app.core.redis import get_redis
        r = await get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}
