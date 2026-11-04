import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import db
from .config import DISABLE_SCHEDULER
from .pipeline import run_collect, run_keyword_check

scheduler = AsyncIOScheduler()
_initial_task: asyncio.Task | None = None


async def _initial_scan() -> None:
    await asyncio.sleep(3)
    await run_keyword_check("startup")
    if db.get_settings().get("auto_collect", True):
        await asyncio.sleep(1)
        await run_collect("startup")


def schedule_jobs() -> None:
    scheduler.remove_all_jobs()
    settings = db.get_settings()
    keyword_interval = max(2, int(settings.get("keyword_interval_minutes", 10)))
    collect_interval = max(5, int(settings.get("collect_interval_minutes", 30)))
    scheduler.add_job(
        run_keyword_check,
        IntervalTrigger(minutes=keyword_interval),
        args=["schedule"],
        id="keyword-scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if settings.get("auto_collect", True):
        scheduler.add_job(
            run_collect,
            IntervalTrigger(minutes=collect_interval),
            args=["schedule"],
            id="topic-collect",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


def start_scheduler() -> None:
    global _initial_task
    if DISABLE_SCHEDULER:
        return
    if not scheduler.running:
        scheduler.start()
    schedule_jobs()
    _initial_task = asyncio.create_task(_initial_scan())


def stop_scheduler() -> None:
    global _initial_task
    if _initial_task and not _initial_task.done():
        _initial_task.cancel()
    if scheduler.running:
        scheduler.shutdown(wait=False)


def scheduler_status() -> dict:
    if DISABLE_SCHEDULER:
        return {"running": False, "disabled": True, "jobs": []}
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append(
            {
                "id": job.id,
                "next_run": next_run.isoformat() if next_run else None,
            }
        )
    return {"running": scheduler.running, "disabled": False, "jobs": jobs}
