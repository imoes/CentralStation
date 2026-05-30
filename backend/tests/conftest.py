"""Stub out infrastructure dependencies before any app module is imported.

All tests run inside the Docker backend container where the app packages are
already installed.  We replace only the parts that would require a live
OpenSearch / PostgreSQL connection.
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock


def _make_module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ── OpenSearch client stub ──────────────────────────────────────────────────
# get_opensearch() is called lazily inside every function; the stub prevents
# connection attempts at import time.
_make_module("app.core.opensearch", get_opensearch=MagicMock())

# ── Database stub ───────────────────────────────────────────────────────────
# AsyncSessionLocal would fail without a real DATABASE_URL env var.
_make_module("app.core.database", AsyncSessionLocal=MagicMock())

# ── ORM model stubs ─────────────────────────────────────────────────────────
# The mapper setup in models/workflow.py and models/alert.py requires a real
# engine.  We stub them so lazy imports inside feed_index.py succeed.
_make_module(
    "app.models.workflow",
    FeedSearch=MagicMock(name="FeedSearch"),
    UserPreference=MagicMock(name="UserPreference"),
    DashboardWidget=MagicMock(name="DashboardWidget"),
    AlertScoreAdjustment=MagicMock(name="AlertScoreAdjustment"),
)
_make_module("app.models.alert", Alert=MagicMock(name="Alert"))
