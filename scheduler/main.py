"""
Scheduler entry point.

This process runs three responsibilities:
1. Tick loop — finds due jobs and creates JobRun + JobAttempt records
2. Outbox publisher — sends pending events to RabbitMQ
3. Watchdog — detects abandoned executions and dead workers

In Phase 1, this is a placeholder that starts and idles.
Logic is added incrementally in Phases 4, 5, and 8.
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

logger = logging.getLogger("scheduler")


def main():
    logger.info("Scheduler starting (Phase 1 — no logic yet)")

    try:
        while True:
            logger.debug("Scheduler tick (idle)")
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Scheduler shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
