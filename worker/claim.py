"""
Worker claim logic — atomically claim a QUEUED attempt with fencing token.

Uses SELECT FOR UPDATE to lock the row, then UPDATE WHERE status = QUEUED
to prevent double-claiming. The fencing_token is a monotonic integer that
guards against stale workers (workers that lost their lease but don't know it).
"""
import logging
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, Worker

logger = logging.getLogger("worker.claim")

LEASE_DURATION_SECONDS = 45

# Priority ordering: higher number = higher priority
PRIORITY_ORDER = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


def claim_attempt(worker_id: str) -> JobAttempt | None:
    """
    Try to claim one QUEUED attempt for this worker.

    Returns the claimed attempt, or None if nothing claimable.

    Algorithm:
    1. Lock a QUEUED attempt with SELECT FOR UPDATE SKIP LOCKED
    2. Verify it's still QUEUED (not claimed by someone else)
    3. Update: set worker_id, fencing_token += 1, lease_expires_at, status=RUNNING
    4. Return the attempt

    The fencing_token is incremented on every claim. If a stale worker
    tries to update using an old token, the WHERE clause fails.
    """
    with transaction.atomic():
        # Find claimable attempts and sort by priority in Python
        candidates = list(
            JobAttempt.objects.select_for_update(skip_locked=True)
            .filter(
                status=JobAttempt.Status.QUEUED,
                scheduled_for__lte=tz.now(),
            )
            .order_by("scheduled_for")[:50]
        )

        if not candidates:
            return None

        # Sort by priority descending (highest first)
        candidates.sort(
            key=lambda a: PRIORITY_ORDER.get(a.priority, 0),
            reverse=True,
        )
        attempt = candidates[0]

        # Atomic claim: only succeeds if still QUEUED
        now = tz.now()
        updated = JobAttempt.objects.filter(
            id=attempt.id,
            status=JobAttempt.Status.QUEUED,  # Fencing check
        ).update(
            status=JobAttempt.Status.RUNNING,
            worker_id=worker_id,
            fencing_token=attempt.fencing_token + 1,
            lease_expires_at=now + timedelta(seconds=LEASE_DURATION_SECONDS),
            started_at=now,
        )

        if updated == 0:
            # Someone else claimed it first
            logger.debug(f"Attempt {attempt.id} was already claimed")
            return None

        # Refresh and return
        attempt.refresh_from_db()
        logger.info(
            f"Claimed attempt {attempt.id} "
            f"(run={attempt.job_run_id}, "
            f"fencing_token={attempt.fencing_token}, "
            f"lease_expires_at={attempt.lease_expires_at})"
        )
        return attempt
