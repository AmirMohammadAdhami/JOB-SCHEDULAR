"""
Scheduler entry point.

This process runs three responsibilities:
1. Tick loop — finds due jobs and creates JobRun + JobAttempt records
2. Outbox publisher — sends pending events to RabbitMQ (Phase 5)
3. Watchdog — detects abandoned executions and dead workers (Phase 8)

The scheduler is stateless — it can crash and restart without data loss.
The database is the source of truth.
"""
import os
import sys
import time
import logging

# Ensure the project root is on sys.path so we can import nexusops and apps
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexusops.settings")

import django  # noqa: E402
django.setup()

from scheduler.tick import tick  # noqa: E402

logger = logging.getLogger("scheduler")

TICK_INTERVAL_SECONDS = 5


def main():
    logger.info(
        f"Scheduler starting (tick every {TICK_INTERVAL_SECONDS}s)"
    )

    try:
        while True:
            try:
                scheduled = tick()
                if scheduled > 0:
                    logger.info(f"Tick: scheduled {scheduled} jobs")
            except Exception as e:
                logger.error(f"Tick failed: {e}", exc_info=True)

            time.sleep(TICK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Scheduler shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
