import logging

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
    from app.core.database import AsyncSessionLocal
    from app.services.ai_agent.graph import run_sysadmin_workflow
    async with AsyncSessionLocal() as db:
        await run_sysadmin_workflow(db)


async def run_network_agent() -> None:
    logger.info("Network AI Agent: starting run")
    from app.core.database import AsyncSessionLocal
    from app.services.ai_agent.network_graph import run_network_workflow
    async with AsyncSessionLocal() as db:
        await run_network_workflow(db)


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


def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler()
    # Alert aggregation every 2 minutes
    _scheduler.add_job(run_alert_aggregation, "interval", minutes=2,
                       id="alert_aggregation", replace_existing=True)
    _scheduler.add_job(run_sysadmin_agent, "interval", minutes=10,
                       id="sysadmin_agent", replace_existing=True)
    _scheduler.add_job(run_network_agent, "interval", minutes=10,
                       id="network_agent", replace_existing=True)
    # Feed housekeeping runs daily at 03:00
    _scheduler.add_job(run_feed_housekeeping, "cron", hour=3, minute=0,
                       id="feed_housekeeping", replace_existing=True)
    _scheduler.start()
    logger.info("APScheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
