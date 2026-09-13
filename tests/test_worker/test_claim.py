"""Tests for worker claim logic."""
import pytest
from datetime import timedelta

from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun, Worker
from apps.jobs.models import Job
from worker.claim import claim_attempt
from worker.heartbeat import (
    register_worker,
    send_heartbeat,
    find_expired_leases,
    mark_worker_dead,
)


@pytest.fixture
def worker(db):
    """Create an active worker."""
    return Worker.objects.create(
        id="test-worker-1",
        hostname="test-host",
        status=Worker.Status.ACTIVE,
        last_heartbeat_at=tz.now(),
    )


@pytest.fixture
def job(db):
    """Create an active job."""
    return Job.objects.create(
        name="claim_test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="billing_reconciliation",
        priority=Job.Priority.HIGH,
        next_run_at=tz.now(),
    )


@pytest.fixture
def queued_attempt(job):
    """Create a QUEUED attempt that is claimable."""
    run = JobRun.objects.create(
        job=job,
        scheduled_for=tz.now(),
        status=JobRun.Status.QUEUED,
    )
    return JobAttempt.objects.create(
        job_run=run,
        job=job,
        attempt_number=1,
        status=JobAttempt.Status.QUEUED,
        priority="HIGH",
        scheduled_for=tz.now(),
        fencing_token=0,
    )


@pytest.mark.django_db
class TestClaimAttempt:
    def test_claim_succeeds(self, queued_attempt, worker):
        claimed = claim_attempt("test-worker-1")
        assert claimed is not None
        assert claimed.id == queued_attempt.id
        assert claimed.status == JobAttempt.Status.RUNNING
        assert claimed.worker_id == "test-worker-1"
        assert claimed.fencing_token == 1
        assert claimed.lease_expires_at > tz.now()

    def test_claim_sets_started_at(self, queued_attempt, worker):
        claimed = claim_attempt("test-worker-1")
        assert claimed.started_at is not None

    def test_claim_returns_none_when_nothing_queued(self, worker):
        claimed = claim_attempt("test-worker-1")
        assert claimed is None

    def test_claim_does_not_claim_future_attempts(self, worker, job):
        """Attempts scheduled in the future should not be claimed."""
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now() + tz.timedelta(hours=1),
            status=JobRun.Status.QUEUED,
        )
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH",
            scheduled_for=tz.now() + tz.timedelta(hours=1),
        )
        claimed = claim_attempt("test-worker-1")
        assert claimed is None

    def test_claim_increments_fencing_token(self, queued_attempt, worker):
        claim_attempt("test-worker-1")
        queued_attempt.refresh_from_db()
        assert queued_attempt.fencing_token == 1

    def test_cannot_claim_already_running_attempt(self, queued_attempt, worker):
        """An attempt already claimed by another worker cannot be re-claimed."""
        # First worker claims it
        claim_attempt("test-worker-1")

        # Second worker tries to claim it
        claimed = claim_attempt("test-worker-2")
        assert claimed is None

    def test_claim_respects_priority_order(self, db, worker, job):
        """Higher priority attempts are claimed first."""
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.QUEUED,
        )
        # Create low priority attempt
        low = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="LOW",
            scheduled_for=tz.now(),
        )
        # Create high priority attempt
        high = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=2,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH",
            scheduled_for=tz.now(),
        )

        claimed = claim_attempt("test-worker-1")
        assert claimed.id == high.id

    def test_different_workers_claim_different_attempts(self, db, job):
        """Two workers should claim different attempts."""
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.QUEUED,
        )
        a1 = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH", scheduled_for=tz.now(),
        )
        a2 = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=2,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH", scheduled_for=tz.now(),
        )

        claimed1 = claim_attempt("worker-1")
        claimed2 = claim_attempt("worker-2")

        assert claimed1 is not None
        assert claimed2 is not None
        assert claimed1.id != claimed2.id


@pytest.mark.django_db
class TestWorkerRegistration:
    def test_register_new_worker(self):
        worker = register_worker("new-worker", "hostname")
        assert worker.id == "new-worker"
        assert worker.hostname == "hostname"
        assert worker.status == Worker.Status.ACTIVE

    def test_register_existing_worker_updates(self):
        register_worker("existing", "host1")
        register_worker("existing", "host2")
        worker = Worker.objects.get(id="existing")
        assert worker.hostname == "host2"


@pytest.mark.django_db
class TestHeartbeat:
    def test_send_heartbeat_updates_worker(self, worker):
        old_heartbeat = worker.last_heartbeat_at
        result = send_heartbeat("test-worker-1")
        worker.refresh_from_db()
        assert worker.last_heartbeat_at > old_heartbeat
        assert result["worker_updated"] is True

    def test_send_heartbeat_extends_leases(self, worker, queued_attempt):
        # Claim the attempt first
        claim_attempt("test-worker-1")
        queued_attempt.refresh_from_db()
        old_lease = queued_attempt.lease_expires_at

        # Send heartbeat
        result = send_heartbeat("test-worker-1")
        queued_attempt.refresh_from_db()
        assert queued_attempt.lease_expires_at > old_lease
        assert result["leases_extended"] == 1

    def test_send_heartbeat_only_extends_valid_leases(self, worker, queued_attempt):
        """Expired leases should not be extended."""
        claim_attempt("test-worker-1")
        queued_attempt.refresh_from_db()

        # Set lease to past
        queued_attempt.lease_expires_at = tz.now() - timedelta(seconds=10)
        queued_attempt.save(update_fields=["lease_expires_at"])

        result = send_heartbeat("test-worker-1")
        assert result["leases_extended"] == 0


@pytest.mark.django_db
class TestExpiredLeases:
    def test_find_expired_leases(self, worker, queued_attempt):
        claim_attempt("test-worker-1")
        queued_attempt.refresh_from_db()

        # Set lease to past
        queued_attempt.lease_expires_at = tz.now() - timedelta(seconds=10)
        queued_attempt.save(update_fields=["lease_expires_at"])

        expired = find_expired_leases()
        assert len(expired) == 1
        assert expired[0].id == queued_attempt.id

    def test_no_expired_leases_when_lease_valid(self, worker, queued_attempt):
        claim_attempt("test-worker-1")
        expired = find_expired_leases()
        assert len(expired) == 0

    def test_mark_worker_dead(self, worker):
        mark_worker_dead("test-worker-1")
        worker.refresh_from_db()
        assert worker.status == Worker.Status.DEAD
