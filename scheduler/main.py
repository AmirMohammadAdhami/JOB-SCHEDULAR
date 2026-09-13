"""
Scheduler entry point.

This process runs three responsibilities:
1. Tick loop — finds due jobs and creates JobRun + JobAttempt records
2. Outbox publisher — sends pending events to RabbitMQ
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
from scheduler.publisher import publish_pending_events  # noqa: E402

logger = logging.getLogger("scheduler")

TICK_INTERVAL_SECONDS = 5
PUBLISHER_INTERVAL_SECONDS = 1


def main():
    logger.info("Scheduler starting")

    tick_count = 0
    publisher_count = 0
    last_tick = 0
    last_publish = 0

    try:
        while True:
            now = time.time()

            # Run tick every 5 seconds
            if now - last_tick >= TICK_INTERVAL_SECONDS:
                try:
                    scheduled = tick()
                    if scheduled > 0:
                        logger.info(f"Tick: scheduled {scheduled} jobs")
                except Exception as e:
                    logger.error(f"Tick failed: {e}", exc_info=True)
                last_tick = now
                tick_count += 1

            # Run publisher every 1 second
            if now - last_publish >= PUBLISHER_INTERVAL_SECONDS:
                try:
                    published = publish_pending_events()
                    if published > 0:
                        logger.info(
                            f"Publisher: sent {published} events"
                        )
                except Exception as e:
                    logger.error(f"Publisher failed: {e}", exc_info=True)
                last_publish = now
                publisher_count += 1

            # Sleep briefly to avoid busy-waiting
            time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info(
            f"Scheduler shutting down "
            f"(ticks={tick_count}, publishes={publisher_count})"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
