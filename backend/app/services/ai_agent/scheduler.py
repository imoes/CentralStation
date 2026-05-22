import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


async def run_sysadmin_agent() -> None:
    logger.info("SysAdmin AI Agent: starting run")
    # Phase 5: LangGraph workflow wird hier importiert und ausgeführt
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
    _scheduler.add_job(run_sysadmin_agent, "interval", minutes=10,
                       id="sysadmin_agent", replace_existing=True)
    _scheduler.add_job(run_network_agent, "interval", minutes=10,
                       id="network_agent", replace_existing=True)
    _scheduler.start()
    logger.info("APScheduler started (sysadmin + network agents every 10 min)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
