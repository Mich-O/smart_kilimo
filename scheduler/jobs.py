"""
scheduler/jobs.py
Background scheduler that fires the daily irrigation cycle at the
configured time (default 06:00 Africa/Nairobi).

IMPORTANT deployment constraint: this scheduler starts once per process,
inside create_app(). If the app is served by more than one worker process
(e.g. `gunicorn --workers 4`), each worker starts its own copy of this
scheduler, and the daily cycle -- and every SMS it sends -- would fire
once per worker. Deploy this with a single worker process (see
Procfile / render.yaml: `--workers 1 --threads 4`, which gives
concurrency without multiplying the scheduler). Scaling beyond one
process would need an external lock (e.g. a Redis-backed leader election)
before adding workers; that's out of scope here.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from services.daily_cycle import run_daily_cycle

logger = logging.getLogger(__name__)


def start_scheduler(sms_client) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Africa/Nairobi")

    def _job():
        logger.info("Scheduled daily irrigation cycle starting")
        run_daily_cycle(sms_client)

    scheduler.add_job(
        func=_job,
        trigger="cron",
        hour=settings.daily_cycle_hour,
        minute=settings.daily_cycle_minute,
        id="daily_irrigation_cycle",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: daily cycle at %02d:%02d Africa/Nairobi",
        settings.daily_cycle_hour,
        settings.daily_cycle_minute,
    )
    return scheduler
