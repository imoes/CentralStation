import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


async def run_alert_aggregation() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.alert_aggregator import run_aggregation
    async with AsyncSessionLocal() as db:
        count = await run_aggregation(db)
    if count:
        logger.info("Alert aggregation: %d new alerts", count)


async def run_sysadmin_agent() -> None:
    logger.info("SysAdmin AI Agent: starting run")
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.user import User
    from app.models.workflow import UserPreference
    from app.services.ai_agent.graph import run_sysadmin_workflow
    from app.services.settings import get_agent_config

    async with AsyncSessionLocal() as db:
        config = await get_agent_config(db)

        # Use the first active sysadmin/admin user's CheckMK preferences as the
        # filter for the scheduled run — avoids duplicate configuration.
        result = await db.execute(
            select(UserPreference)
            .join(User, User.id == UserPreference.user_id)
            .where(User.role.in_(["admin", "sysadmin"]), User.is_active.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
        prefs = result.scalar_one_or_none()
        locs  = (prefs.checkmk_locations   or []) if prefs else []
        ve    = (prefs.checkmk_ve          or []) if prefs else []
        crit  = (prefs.checkmk_criticality or []) if prefs else []
        os_   = (prefs.checkmk_os          or []) if prefs else []

        await run_sysadmin_workflow(
            db,
            min_age_minutes=config.interval_minutes,
            user_checkmk_locations=locs  or None,
            user_checkmk_ve=ve            or None,
            user_checkmk_criticality=crit or None,
            user_checkmk_os=os_           or None,
        )


async def run_network_agent() -> None:
    logger.info("Network AI Agent: starting run")
    from app.core.database import AsyncSessionLocal
    from app.services.ai_agent.network_graph import run_network_workflow
    async with AsyncSessionLocal() as db:
        await run_network_workflow(db)


async def run_metrics_collection() -> None:
    """Collect CheckMK RRD metrics for active hosts → cs-metrics-checkmk."""
    from app.services.metrics_collector import collect_checkmk_metrics
    await collect_checkmk_metrics()


async def run_worklist_build() -> None:
    """Build the AI-prioritised worklist for the bridge."""
    from app.core.database import AsyncSessionLocal
    from app.services.worklist_builder import build_worklist
    from app.services.settings import get_agent_config
    async with AsyncSessionLocal() as db:
        cfg = await get_agent_config(db)
        await build_worklist(db, hours=24, size=cfg.worklist_size)


async def run_generative_refresh() -> None:
    """Re-compose every active generative dashboard from the current situation.

    The situation is global, so the LLM is invoked ONCE per run and the resulting
    spec is applied to all users' generative dashboards — keeps llama.cpp load low."""
    import uuid as _uuid
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.workflow import Dashboard, DashboardWidget
    from app.services.dashboard.generative_designer import design_dashboard, GENERATIVE_DASHBOARD_NAME

    async with AsyncSessionLocal() as db:
        # Only the reserved AI-singleton dashboards — never a user's hand-built one,
        # even if its mode column was mislabelled by an earlier version.
        result = await db.execute(
            select(Dashboard).where(Dashboard.name == GENERATIVE_DASHBOARD_NAME)
        )
        dashboards = result.scalars().all()
        if not dashboards:
            return
        # One LLM call for the shared situation
        spec = await design_dashboard(db, str(dashboards[0].user_id))
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        for dash in dashboards:
            existing = await db.execute(
                select(DashboardWidget).where(DashboardWidget.dashboard_id == dash.id)
            )
            for w in existing.scalars().all():
                await db.delete(w)
            await db.flush()
            for spec_w in spec["widgets"]:
                db.add(DashboardWidget(
                    id=_uuid.uuid4(), user_id=dash.user_id, dashboard_id=dash.id,
                    widget_type=spec_w["widget_type"], title=spec_w["title"],
                    gs_x=spec_w["gs_x"], gs_y=spec_w["gs_y"],
                    gs_w=spec_w["gs_w"], gs_h=spec_w["gs_h"], config=spec_w["config"],
                ))
            dash.rationale = spec.get("rationale") or ""
            dash.generated_at = now
        await db.commit()
        logger.info("Generative refresh: recomposed %d dashboard(s)", len(dashboards))


async def run_score_housekeeping() -> None:
    """Expire stale score adjustments and detect long-ignored alerts."""
    from app.core.database import AsyncSessionLocal
    from app.services.alert_score_learner import cleanup_expired_adjustments, record_alert_ignored
    from app.models.alert import Alert
    from sqlalchemy import select
    from datetime import datetime, timezone, timedelta

    async with AsyncSessionLocal() as db:
        # 1. Reset expired deltas
        await cleanup_expired_adjustments(db)

        # 2. Alerts open >4h without ticket or ack → they were important
        cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
        result = await db.execute(
            select(Alert).where(
                Alert.status == "new",
                Alert.created_at <= cutoff,
                Alert.acknowledged_by.is_(None),
            ).limit(50)
        )
        for a in result.scalars().all():
            await record_alert_ignored({
                "source": a.source,
                "title": a.title,
                "severity": a.severity,
                "external_id": a.external_id,
                "metadata": a.metadata_ or {},
            }, db)


async def run_feed_housekeeping() -> None:
    """Delete feed items older than per-source retention (from global_settings)."""
    from app.core.database import AsyncSessionLocal
    from app.services.feed_index import delete_old_items, ALL_SOURCES
    from app.services.settings import get_all_settings

    async with AsyncSessionLocal() as db:
        s = await get_all_settings(db)

    total = 0
    for source in ALL_SOURCES:
        days = int(s.get(f"feed.retention.{source}_days") or 90)
        deleted = await delete_old_items(source, days)
        total += deleted

    if total:
        logger.info("Feed housekeeping: removed %d old items total", total)


async def start_scheduler() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_agent_config

    global _scheduler
    async with AsyncSessionLocal() as db:
        config = await get_agent_config(db)

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(run_alert_aggregation, "interval",
                       minutes=config.aggregation_interval_minutes,
                       id="alert_aggregation", replace_existing=True)
    _scheduler.add_job(run_sysadmin_agent, "interval",
                       minutes=config.interval_minutes,
                       id="sysadmin_agent", replace_existing=True)
    _scheduler.add_job(run_network_agent, "interval",
                       minutes=config.interval_minutes,
                       id="network_agent", replace_existing=True)
    _scheduler.add_job(run_metrics_collection, "interval",
                       minutes=5, id="metrics_collection", replace_existing=True)
    _scheduler.add_job(run_worklist_build, "interval",
                       minutes=config.worklist_interval_minutes, id="worklist_build",
                       replace_existing=True, next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30))
    _scheduler.add_job(run_generative_refresh, "interval",
                       minutes=config.generative_interval_minutes, id="generative_refresh",
                       replace_existing=True, next_run_time=datetime.now(timezone.utc) + timedelta(seconds=45))
    _scheduler.add_job(run_score_housekeeping, "cron", hour=2, minute=0,
                       id="score_housekeeping", replace_existing=True)
    _scheduler.add_job(run_feed_housekeeping, "cron", hour=3, minute=0,
                       id="feed_housekeeping", replace_existing=True)
    _scheduler.start()
    logger.info(
        "APScheduler started — aggregation: %dmin, agent: %dmin, metrics: 5min",
        config.aggregation_interval_minutes, config.interval_minutes,
    )


async def reschedule_jobs() -> None:
    """Re-read interval settings and update running scheduler jobs."""
    global _scheduler
    if not _scheduler or not _scheduler.running:
        return
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_agent_config
    from apscheduler.triggers.interval import IntervalTrigger

    async with AsyncSessionLocal() as db:
        config = await get_agent_config(db)

    _scheduler.reschedule_job(
        "alert_aggregation",
        trigger=IntervalTrigger(minutes=config.aggregation_interval_minutes),
    )
    _scheduler.reschedule_job(
        "sysadmin_agent",
        trigger=IntervalTrigger(minutes=config.interval_minutes),
    )
    _scheduler.reschedule_job(
        "network_agent",
        trigger=IntervalTrigger(minutes=config.interval_minutes),
    )
    if _scheduler.get_job("generative_refresh"):
        _scheduler.reschedule_job(
            "generative_refresh",
            trigger=IntervalTrigger(minutes=config.generative_interval_minutes),
        )
    logger.info(
        "Jobs rescheduled — aggregation: %dmin, agent: %dmin",
        config.aggregation_interval_minutes, config.interval_minutes,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
