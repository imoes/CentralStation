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
    # Phase 5: LangGraph workflow
    # from app.services.ai_agent.graph import sysadmin_graph
    # await sysadmin_graph.ainvoke(...)


async def run_network_agent() -> None:
    logger.info("Network AI Agent: starting run")
    # Phase 6: Network LangGraph workflow
    # from app.services.ai_agent.network_graph import network_graph
    # await network_graph.ainvoke(...)


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
    _scheduler.start()
    logger.info("APScheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
