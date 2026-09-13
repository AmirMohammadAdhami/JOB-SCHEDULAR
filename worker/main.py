"""
Worker entry point.

This process:
1. Registers itself in the worker table
2. Polls for claimable job attempts
3. Claims attempts atomically with fencing token
4. Executes jobs in background threads
5. Sends heartbeats to maintain its lease
6. Updates attempt status on completion/failure

The worker is designed to be simple and robust:
- Polls DB every 2s (no RabbitMQ consumer needed for basic operation)
- Heartbeats every 15s extend leases
- Failed attempts get retried by the scheduler (Phase 8)
"""
import os
import signal
import socket
import sys
import time
import logging
from concurrent.futures import Future
from datetime import timedelta
from apps.executions.models import JobAttempt, JobRun
from worker.claim import claim_attempt
from worker.executor import submit_job
from worker.heartbeat import (
    HEARTBEAT_INTERVAL_SECONDS,
    register_worker,
    send_heartbeat,
)
import django

# Ensure the project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexusops.settings")


django.setup()



logger = logging.getLogger("worker")

POLL_INTERVAL_SECONDS = 2
WORKER_HOSTNAME = socket.gethostname()


def generate_worker_id() -> str:
    """Generate a unique worker ID from hostname and PID."""
    return f"worker-{WORKER_HOSTNAME}-{os.getpid()}"


def handle_attempt_completion(
    attempt: JobAttempt,
    future: Future,
    worker_id: str,
):
    """Called when an attempt finishes execution."""
    try:
        result = future.result()
    except Exception as e:
        result = {
            "success": False,
            "duration_ms": 0,
            "error": str(e),
        }

    now = django.utils.timezone.now()

    if result["success"]:
        # Mark attempt as SUCCESS
        duration = timedelta(milliseconds=result["duration_ms"])
        JobAttempt.objects.filter(id=attempt.id).update(
            status=JobAttempt.Status.SUCCESS,
            finished_at=now,
            duration=duration,
        )
        # Update run status
        _update_run_status(attempt.job_run_id)
        logger.info(
            f"Attempt {attempt.id} succeeded "
            f"({result['duration_ms']}ms)"
        )
    else:
        # Mark attempt as FAILED
        duration = timedelta(milliseconds=result["duration_ms"])
        JobAttempt.objects.filter(id=attempt.id).update(
            status=JobAttempt.Status.FAILED,
            finished_at=now,
            error_message=result.get("error", "Unknown error"),
            duration=duration,
        )
        # Update run status
        _update_run_status(attempt.job_run_id)
        logger.error(
            f"Attempt {attempt.id} failed: {result.get('error')}"
        )


def _update_run_status(run_id):
    """Update job run status based on its attempts."""
    run = JobRun.objects.get(id=run_id)
    attempts = JobAttempt.objects.filter(job_run_id=run_id)

    # Check if any attempt succeeded
    if attempts.filter(status=JobAttempt.Status.SUCCESS).exists():
        run.status = JobRun.Status.SUCCESS
        run.finished_at = django.utils.timezone.now()
        run.save(update_fields=["status", "finished_at"])
        return

    # Check if all attempts are done (failed or cancelled)
    active_statuses = [
        JobAttempt.Status.QUEUED,
        JobAttempt.Status.RUNNING,
    ]
    if not attempts.filter(status__in=active_statuses).exists():
        # All attempts done, none succeeded
        run.status = JobRun.Status.PERMANENTLY_FAILED
        run.finished_at = django.utils.timezone.now()
        run.save(update_fields=["status", "finished_at"])


def main():
    worker_id = generate_worker_id()
    logger.info(f"Worker starting: {worker_id}")

    # Register worker
    register_worker(worker_id, WORKER_HOSTNAME)

    # Track running futures
    running_futures: dict[str, Future] = {}

    # Heartbeat timer
    last_heartbeat = 0

    try:
        while True:
            now = time.time()

            # Send heartbeat
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                try:
                    send_heartbeat(worker_id)
                except Exception as e:
                    logger.error(f"Heartbeat failed: {e}")
                last_heartbeat = now

            # Clean up completed futures
            completed = []
            for attempt_id, future in running_futures.items():
                if future.done():
                    completed.append(attempt_id)
            for attempt_id in completed:
                del running_futures[attempt_id]

            # Check concurrency limit
            if len(running_futures) >= 4:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Try to claim an attempt
            try:
                attempt = claim_attempt(worker_id)
                if attempt is None:
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # Submit for execution
                future = submit_job(
                    handler_name=attempt.job.handler,
                    attempt_id=str(attempt.id),
                    timeout_seconds=attempt.job.timeout_seconds,
                )
                running_futures[str(attempt.id)] = future

                # Add callback for completion
                future.add_done_callback(
                    lambda f, a=attempt, w=worker_id: (
                        handle_attempt_completion(a, f, w)
                    )
                )

            except Exception as e:
                logger.error(f"Claim/execute failed: {e}", exc_info=True)

            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info(
            f"Worker shutting down "
            f"(running={len(running_futures)})"
        )
        # Mark worker as dead
        from worker.heartbeat import mark_worker_dead
        mark_worker_dead(worker_id)
        sys.exit(0)


if __name__ == "__main__":
    main()
