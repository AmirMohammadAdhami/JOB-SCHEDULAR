"""
Worker entry point.

This process:
1. Registers itself in the worker table
2. Polls for claimable job attempts
3. Claims attempts atomically
4. Executes jobs in isolated subprocesses
5. Sends heartbeats to maintain its lease

In Phase 1, this is a placeholder that starts and idles.
Logic is added incrementally in Phases 6, 7, and 9.
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

logger = logging.getLogger("worker")


def main():
    logger.info("Worker starting (Phase 1 — no logic yet)")

    try:
        while True:
            logger.debug("Worker loop (idle)")
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("Worker shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
