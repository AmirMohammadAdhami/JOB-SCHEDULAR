"""
Heartbeat and lease management.

The worker sends heartbeats every 15s to:
1. Update its last_heartbeat_at timestamp
2. Extend leases on running attempts (lease = 45s)

If a worker dies, its leases expire after 45s and the scheduler's watchdog
(Phase 8) will reschedule those attempts.
"""
import logging
from datetime import timedelta

from django.utils import timezone as tz

from apps.executions.models import JobAttempt, Worker

logger = logging.getLogger("worker.heartbeat")

HEARTBEAT_INTERVAL_SECONDS = 15
LEASE_DURATION_SECONDS = 45


def register_worker(worker_id: str, hostname: str) -> Worker:
    """Register or update a worker in the database."""
    worker, created = Worker.objects.update_or_create(
        id=worker_id,
        defaults={
            "hostname": hostname,
            "status": Worker.Status.ACTIVE,
            "last_heartbeat_at": tz.now(),
        },
    )
    if created:
        logger.info(f"Registered new worker: {worker_id}")
    else:
        logger.debug(f"Updated worker heartbeat: {worker_id}")
    return worker


def send_heartbeat(worker_id: str) -> dict:
    """
    Send a heartbeat: update worker timestamp and extend leases.

    Returns:
        {
            "worker_updated": bool,
            "leases_extended": int,
        }
    """
    now = tz.now()
    result = {"worker_updated": False, "leases_extended": 0}

    # Update worker heartbeat
    updated = Worker.objects.filter(id=worker_id).update(
        last_heartbeat_at=now,
        status=Worker.Status.ACTIVE,
    )
    result["worker_updated"] = updated > 0

    # Extend leases on running attempts claimed by this worker
    new_lease = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    extended = JobAttempt.objects.filter(
        worker_id=worker_id,
        status=JobAttempt.Status.RUNNING,
        lease_expires_at__gt=now,  # Only extend valid leases
    ).update(lease_expires_at=new_lease)
    result["leases_extended"] = extended

    logger.debug(
        f"Heartbeat: worker={worker_id}, "
        f"leases_extended={extended}"
    )
    return result


def find_expired_leases() -> list[JobAttempt]:
    """
    Find attempts whose lease has expired.

    These are "zombie" attempts — the worker died without reporting
    completion. They need to be rescheduled by the watchdog (Phase 8).
    """
    return list(
        JobAttempt.objects.filter(
            status=JobAttempt.Status.RUNNING,
            lease_expires_at__lt=tz.now(),
        )
    )


def mark_worker_dead(worker_id: str):
    """Mark a worker as dead."""
    Worker.objects.filter(id=worker_id).update(
        status=Worker.Status.DEAD,
    )
    logger.warning(f"Worker marked dead: {worker_id}")
