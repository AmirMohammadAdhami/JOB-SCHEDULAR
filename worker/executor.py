"""
Job execution — runs job handlers in background threads.

Uses ThreadPoolExecutor so the worker can still send heartbeats
while jobs are running. The handler runs in-process but in a separate
thread, which is sufficient for development and testing.

For production, this can be upgraded to subprocess isolation.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from apps.jobs.handlers import get_handler

logger = logging.getLogger("worker.executor")

# Limit concurrent jobs per worker
MAX_CONCURRENT_JOBS = 4

# Global executor (one per worker process)
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)


def execute_handler(
    handler_name: str,
    attempt_id: str,
    timeout_seconds: int = 300,
    job_kwargs: dict | None = None,
) -> dict:
    """
    Execute a handler synchronously (called from the executor thread).

    Returns:
        {
            "success": bool,
            "duration_ms": int,
            "output": str,
            "error": str | None,
        }
    """
    start = time.monotonic()

    try:
        handler = get_handler(handler_name)
        result = handler(attempt_id=attempt_id, **(job_kwargs or {}))

        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            f"Handler {handler_name} completed in {duration_ms}ms: {result}"
        )
        return {
            "success": True,
            "duration_ms": duration_ms,
            "output": str(result),
            "error": None,
        }

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"Handler {handler_name} failed after {duration_ms}ms: {e}"
        )
        return {
            "success": False,
            "duration_ms": duration_ms,
            "output": "",
            "error": str(e),
        }


def submit_job(
    handler_name: str,
    attempt_id: str,
    timeout_seconds: int = 300,
    job_kwargs: dict | None = None,
) -> "Future":
    """
    Submit a job for background execution.

    Returns a Future that can be checked for completion.
    """
    future = _executor.submit(
        execute_handler,
        handler_name,
        attempt_id,
        timeout_seconds,
        job_kwargs,
    )
    logger.info(f"Submitted job {handler_name} (attempt={attempt_id})")
    return future
