"""Background polling.

The job is intentionally boring: poll the enabled sources, then evaluate and
dispatch alerts. No queue, no workers, no broker. A daily figure from one
government page plus a handful of sensors does not need any of that, and the
operational cost of a message broker would be larger than the whole problem.

Two behaviours are load-bearing:

* **A catch-up run on boot.** A deploy or a crash otherwise leaves a silent gap
  in the data -- nobody notices until somebody asks why a Tuesday is missing.
  Because ingest is idempotent, running it on every boot costs nothing when
  there is nothing to catch up on.

* **The job never raises.** An exception escaping into APScheduler kills the
  job for the rest of the process lifetime, and the API would keep serving
  cheerfully with a frozen dataset. Failures are recorded as unhealthy
  outcomes and swallowed here.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from hawa.config import get_config
from hawa.db import session_scope

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def poll_once() -> None:
    """One full cycle: ingest, evaluate thresholds, deliver alerts."""
    from hawa.alerts.engine import dispatch_pending, evaluate
    from hawa.ingest import ingest_enabled_sources

    try:
        reports = ingest_enabled_sources()
        for report in reports:
            log.info("%s", report.summary())
    except Exception:
        log.exception("ingest cycle failed")
        return

    try:
        with session_scope() as session:
            created = evaluate(session)
            if created:
                log.info("%s new alert(s) queued", len(created))
        with session_scope() as session:
            delivered, failed = dispatch_pending(session)
            if delivered or failed:
                log.info("alerts delivered=%s failed=%s", delivered, failed)
    except Exception:
        # Alerting is best-effort. Data is already safely stored above, and
        # losing an alert is recoverable in a way that losing a day is not.
        log.exception("alert stage failed; ingested data is unaffected")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    config = get_config()
    if not config.enable_scheduler:
        log.info("scheduler disabled (HAWA_ENABLE_SCHEDULER=false)")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="Asia/Karachi")
    scheduler.add_job(
        poll_once,
        "interval",
        minutes=config.poll_interval_minutes,
        id="poll",
        # If the process was busy or asleep, run once on wake rather than
        # firing every missed interval at once.
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info("scheduler started, polling every %s min", config.poll_interval_minutes)

    # Catch-up run, once per boot.
    scheduler.add_job(poll_once, "date", id="boot_catchup", misfire_grace_time=300)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
