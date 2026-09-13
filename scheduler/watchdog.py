"""
Watchdog — detects abandoned executions and dead workers.

Runs every 10 seconds in the scheduler process.

Responsibilities:
1. Detect expired leases (worker died without reporting)
2. Reschedule failed attempts (if retry_limit allows)
3. Mark dead workers (no heartbeat for 60s)
4. Mark permanently failed runs (all retries exhausted)
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun, Worker
from apps.jobs.models import Job

logger = logging.getLogger("scheduler.watchdog")

HEARTBEAT_TIMEOUT_SECONDS = 60


def run_watchdog() -> dict:
    """
    Run one watchdog cycle.

    Returns:
        {
            "expired_leases": int,
            "rescheduled": int,
            "permanently_failed": int,
            "dead_workers": int,
        }
    """
    result = {
        "expired_leases": 0,
        "rescheduled": 0,
        "permanently_failed": 0,
        "dead_workers": 0,
    }

    now = tz.now()

    # 1. Find and handle expired leases
    expired = _find_expired_leases(now)
    result["expired_leases"] = len(expired)

    for attempt in expired:
        rescheduled = _handle_expired_attempt(attempt, now)
        if rescheduled:
            result["rescheduled"] += 1

    # 2. Mark permanently failed runs
    result["permanently_failed"] = _mark_permanently_failed(now)

    # 3. Mark dead workers
    result["dead_workers"] = _mark_dead_workers(now)

    if any(v > 0 for v in result.values()):
        logger.info(
            f"Watchdog: expired={result['expired_leases']}, "
            f"rescheduled={result['rescheduled']}, "
            f"permanently_failed={result['permanently_failed']}, "
            f"dead_workers={result['dead_workers']}"
        )

    return result


def _find_expired_leases(now) -> list[JobAttempt]:
    """Find attempts with expired leases."""
    return list(
        JobAttempt.objects.filter(
            status=JobAttempt.Status.RUNNING,
            lease_expires_at__lt=now,
        )
    )


def _handle_expired_attempt(attempt: JobAttempt, now) -> bool:
    """
    Handle an expired attempt — either reschedule or fail.

    Returns True if rescheduled.
    """
    with transaction.atomic():
        # Refresh and check it's still expired (prevents race condition)
        attempt.refresh_from_db()
        if attempt.status != JobAttempt.Status.RUNNING:
            return False
        if attempt.lease_expires_at >= now:
            return False

        # Mark as TIMED_OUT
        JobAttempt.objects.filter(id=attempt.id).update(
            status=JobAttempt.Status.TIMED_OUT,
            finished_at=now,
            error_message="Lease expired — worker likely crashed",
        )

        # Check if we can retry
        job = attempt.job
        total_attempts = JobAttempt.objects.filter(
            job_run_id=attempt.job_run_id
        ).count()

        if total_attempts < job.retry_limit:
            # Create a new attempt
            new_attempt = JobAttempt.objects.create(
                job_run=attempt.job_run,
                job=job,
                attempt_number=total_attempts + 1,
                status=JobAttempt.Status.QUEUED,
                priority=job.priority,
                scheduled_for=now,
                fencing_token=0,
            )
            logger.info(
                f"Rescheduled attempt {attempt.id} -> "
                f"new attempt {new_attempt.id} "
                f"(attempt {total_attempts + 1}/{job.retry_limit})"
            )
            return True
        else:
            logger.info(
                f"Attempt {attempt.id} exhausted retries "
                f"({total_attempts}/{job.retry_limit})"
            )
            return False


def _mark_permanently_failed(now) -> int:
    """
    Mark runs as PERMANENTLY_FAILED if all attempts are done and none succeeded.

    Returns the number of runs marked.
    """
    count = 0

    # Find QUEUED or RUNNING runs where all attempts are terminal
    runs = JobRun.objects.filter(
        status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING],
    )

    for run in runs:
        attempts = JobAttempt.objects.filter(job_run_id=run.id)

        # Skip if there are still active attempts
        active_statuses = [
            JobAttempt.Status.QUEUED,
            JobAttempt.Status.RUNNING,
        ]
        if attempts.filter(status__in=active_statuses).exists():
            continue

        # Check if any attempt succeeded
        if attempts.filter(status=JobAttempt.Status.SUCCESS).exists():
            if run.status != JobRun.Status.SUCCESS:
                run.status = JobRun.Status.SUCCESS
                run.finished_at = now
                run.save(update_fields=["status", "finished_at"])
                count += 1
            continue

        # All attempts done, none succeeded — mark as permanently failed
        if run.status != JobRun.Status.PERMANENTLY_FAILED:
            run.status = JobRun.Status.PERMANENTLY_FAILED
            run.finished_at = now
            run.save(update_fields=["status", "finished_at"])
            count += 1
            logger.info(f"Run {run.id} marked as PERMANENTLY_FAILED")

    return count


def _mark_dead_workers(now) -> int:
    """
    Mark workers as DEAD if no heartbeat for 60s.

    Returns the number of workers marked dead.
    """
    cutoff = now - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)
    dead = Worker.objects.filter(
        status=Worker.Status.ACTIVE,
        last_heartbeat_at__lt=cutoff,
    ).update(status=Worker.Status.DEAD)

    if dead > 0:
        logger.warning(f"Marked {dead} workers as DEAD")

    return dead
