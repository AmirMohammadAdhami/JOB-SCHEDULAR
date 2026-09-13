"""
Scheduler tick — finds due jobs and creates JobRun + JobAttempt records.

This module implements the core scheduling logic:

1. Find active jobs whose next_run_at <= now
2. Lock them with FOR UPDATE SKIP LOCKED (prevents duplicate scheduling)
3. For each locked job:
   a. Check idempotency (no duplicate run for same scheduled_for)
   b. Create JobRun + JobAttempt in same transaction
   c. Update job.next_run_at to next occurrence
   d. Create OutboxEvent in same transaction
4. Commit everything atomically

The database IS the coordination mechanism. No Redis lock needed.
"""
import logging
from croniter import croniter
from django.db import transaction
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun, OutboxEvent
from apps.jobs.models import Job

logger = logging.getLogger("scheduler")

# Maximum jobs to process per tick to prevent runaway processing
MAX_JOBS_PER_TICK = 50


def calculate_next_run(job: Job, after: tz.datetime) -> tz.datetime:
    """Calculate the next run time for a job."""
    if job.schedule_type == Job.ScheduleType.ONE_TIME:
        # One-time jobs don't reschedule — set to NULL
        return None

    # CRON: use croniter to get next occurrence
    cron = croniter(job.cron_expression, after)
    next_run = cron.get_next(tz.datetime)

    # Ensure timezone awareness
    if tz.is_naive(next_run):
        # croniter returns naive datetime — attach the job's timezone
        import zoneinfo
        try:
            tz_info = zoneinfo.ZoneInfo(job.timezone)
            next_run = next_run.replace(tzinfo=tz_info)
        except (ValueError, zoneinfo.ZoneInfoNotFoundError):
            next_run = next_run.replace(tzinfo=tz.UTC)

    return next_run


def create_run_for_job(job: Job, now: tz.datetime) -> bool:
    """
    Create a JobRun + JobAttempt for a single job, inside a transaction.

    Returns True if a run was created, False if skipped (idempotency).
    """
    scheduled_for = job.next_run_at or now

    # Idempotency check: don't create duplicate run for same scheduled time
    existing = JobRun.objects.filter(
        job=job,
        scheduled_for=scheduled_for,
    ).exists()

    if existing:
        logger.debug(
            f"Skipping job {job.name}: run already exists for {scheduled_for}"
        )
        return False

    # Create JobRun
    run = JobRun.objects.create(
        job=job,
        status=JobRun.Status.QUEUED,
        scheduled_for=scheduled_for,
    )

    # Create first JobAttempt
    attempt = JobAttempt.objects.create(
        job_run=run,
        job=job,
        attempt_number=1,
        status=JobAttempt.Status.QUEUED,
        priority=job.priority,
        scheduled_for=scheduled_for,
    )

    # Create OutboxEvent for worker notification
    OutboxEvent.objects.create(
        event_type="job_attempt_created",
        aggregate_id=run.id,
        payload={
            "run_id": str(run.id),
            "attempt_id": str(attempt.id),
            "job_id": str(job.id),
            "priority": job.priority,
        },
    )

    logger.info(
        f"Scheduled job {job.name}: run={run.id}, attempt={attempt.id}, "
        f"scheduled_for={scheduled_for}"
    )
    return True


def tick() -> int:
    """
    Execute one scheduler tick.

    Returns the number of jobs scheduled in this tick.
    """
    now = tz.now()
    scheduled_count = 0

    with transaction.atomic():
        # Find due jobs and lock them atomically
        # FOR UPDATE SKIP LOCKED: if another scheduler locked a row, skip it
        due_jobs = list(
            Job.objects.select_for_update(skip_locked=True).filter(
                status=Job.Status.ACTIVE,
                next_run_at__lte=now,
            )
            .order_by("priority", "next_run_at")
            [:MAX_JOBS_PER_TICK]
        )

        if not due_jobs:
            logger.debug("No due jobs found")
            return 0

        logger.info(f"Found {len(due_jobs)} due jobs")

        for job in due_jobs:
            try:
                # Calculate next_run_at BEFORE creating the run
                # (so the job's next_run_at is updated even if creation fails)
                new_next_run = calculate_next_run(job, now)

                # Create run + attempt + outbox event
                created = create_run_for_job(job, now)

                if created:
                    scheduled_count += 1

                # Update job's next_run_at and last_run_at
                job.next_run_at = new_next_run
                job.last_run_at = now
                job.save(update_fields=["next_run_at", "last_run_at"])

            except Exception as e:
                logger.error(
                    f"Error scheduling job {job.name}: {e}", exc_info=True
                )
                continue

    logger.info(f"Scheduler tick complete: {scheduled_count} jobs scheduled")
    return scheduled_count
