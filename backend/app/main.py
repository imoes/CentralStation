import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware


def _configure_logging() -> None:
    """Configure application logging so app.* loggers reliably reach stdout.

    Without this, uvicorn only configures its own loggers and the application
    loggers (app.services.*, app.api.*) fall back to the root logger which has
    no handler under uvicorn → warnings/errors silently vanished (this is why
    'nothing showed in the logs'). Level via LOG_LEVEL env (default INFO).
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()  # → stdout/stderr (captured by docker)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    app_logger.propagate = False
    # Avoid duplicate handlers on reload
    app_logger.handlers = [handler]

    # Root: keep WARNING for third-party noise, but ensure a handler exists.
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("app.main")

from app.api import auth, users, connectors, alerts, kanban, network, ai, ws, audit, preferences, jira_view, workflow, feed, feed_searches, dashboard_widgets, bridge, help as help_router, hosts, tickets, topology, skills as skills_router
from app.api import settings as settings_router
from app.api import oauth_providers, computer_proxy, remediation, ide, awx_ng, projects as projects_router
from app.api.mcp_server import mcp
from app.core.config import settings
from app.core.opensearch import close_opensearch
from app.core.rate_limit import limiter
from app.core.redis import close_redis


# Streamable-HTTP MCP app for native MCP clients (codex / claude CLI). Mounted
# alongside the legacy SSE app (mcp.sse_app(), used by Hermes) further below.
# Unlike sse_app(), the streamable-http app runs a session manager that must be
# started via its lifespan — we nest it inside the main app lifespan so the
# StreamableHTTPSessionManager task group is initialized.
mcp_http_app = mcp.http_app(path="/", transport="streamable-http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.ai_agent.scheduler import start_scheduler, stop_scheduler
    from app.services.feed_index import ensure_indices

    # Start the MCP streamable-http session manager for the whole app lifetime.
    async with mcp_http_app.lifespan(app):
        await start_scheduler()
        # Ensure OpenSearch feed indices exist, then backfill existing DB alerts
        try:
            await ensure_indices()
            from app.services.feed_index import backfill_from_db
            await backfill_from_db(days=7)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("OpenSearch index setup deferred: %s", exc)
        # Ensure Living Documentation indices (cs-knowledge, cs-skills)
        try:
            from app.services.knowledge_index import ensure_knowledge_indices
            await ensure_knowledge_indices()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Knowledge index setup deferred: %s", exc)
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
app.include_router(bridge.router, prefix="/api")
app.include_router(help_router.router, prefix="/api")
app.include_router(hosts.router, prefix="/api")
app.include_router(tickets.router, prefix="/api")
app.include_router(oauth_providers.router, prefix="/api")
app.include_router(computer_proxy.router, prefix="/api")
app.include_router(topology.router, prefix="/api")
app.include_router(remediation.router, prefix="/api")
app.include_router(ide.router, prefix="/api")
app.include_router(awx_ng.router, prefix="/api")
app.include_router(projects_router.router, prefix="/api")
app.include_router(skills_router.router, prefix="/api")
app.include_router(ws.router, prefix="/api")

# fastmcp sse_app() has no HEAD handler — Hermes probes with HEAD before
# connecting, which causes a TypeError. Intercept it here first.
@app.head("/api/mcp/sse")
async def mcp_sse_head():
    from fastapi.responses import Response
    return Response(status_code=200)

app.mount("/api/mcp", mcp.sse_app())
# Streamable-HTTP MCP endpoint at /api/mcp-http/ for native MCP clients (codex /
# claude CLI). Same tools as the SSE app, but a transport modern clients speak
# directly without a stdio bridge. Lifespan is started in the app lifespan above.
app.mount("/api/mcp-http", mcp_http_app)


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
