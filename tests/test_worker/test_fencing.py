"""
Tests for fencing token mechanism.

Fencing tokens prevent stale workers from modifying attempts after their
lease expires or another worker has claimed the attempt.
"""
import pytest
from datetime import timedelta
from unittest.mock import MagicMock

from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun, Worker
from apps.jobs.models import Job
from scheduler.tick import tick
from scheduler.watchdog import run_watchdog
from worker.claim import claim_attempt
from worker.executor import execute_handler
from worker.main import handle_attempt_completion


@pytest.fixture
def job(db):
    return Job.objects.create(
        name="fencing_test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="billing_reconciliation",
        priority=Job.Priority.HIGH,
        retry_limit=3,
        next_run_at=tz.now(),
    )


@pytest.fixture
def worker_a(db):
    return Worker.objects.create(
        id="worker-a",
        hostname="host-a",
        status=Worker.Status.ACTIVE,
        last_heartbeat_at=tz.now(),
    )


@pytest.fixture
def worker_b(db):
    return Worker.objects.create(
        id="worker-b",
        hostname="host-b",
        status=Worker.Status.ACTIVE,
        last_heartbeat_at=tz.now(),
    )


@pytest.mark.django_db
class TestFencingTokenGeneration:
    def test_new_attempt_has_token_zero(self, job):
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.QUEUED
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH", scheduled_for=tz.now(),
        )
        assert attempt.fencing_token == 0

    def test_claim_increments_token(self, job, worker_a):
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.QUEUED
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH", scheduled_for=tz.now(),
            fencing_token=0,
        )
        claimed = claim_attempt("worker-a")
        assert claimed.fencing_token == 1

    def test_watchdog_retry_starts_at_zero(self, job, worker_a):
        """A retry attempt created by watchdog starts with token=0."""
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
            fencing_token=1,
        )
        # Watchdog creates retry
        run_watchdog()

        retry = JobAttempt.objects.filter(job_run=run).exclude(
            id=attempt.id
        ).first()
        assert retry is not None
        assert retry.fencing_token == 0
        assert retry.status == JobAttempt.Status.QUEUED


@pytest.mark.django_db
class TestFencingTokenValidation:
    def test_successful_update_with_correct_token(self, job, worker_a):
        """Worker can update attempt with matching fencing_token."""
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            fencing_token=1,
        )
        result = {
            "success": True, "duration_ms": 100,
            "output": "ok", "error": None,
        }
        future = MagicMock()
        future.result.return_value = result

        handle_attempt_completion(attempt, future, "worker-a")
        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.SUCCESS

    def test_stale_update_rejected_with_wrong_token(self, job, worker_a):
        """Worker with wrong fencing_token cannot update attempt."""
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            fencing_token=1,
        )

        # Simulate stale worker with old token trying to update
        stale_attempt = JobAttempt(
            id=attempt.id, fencing_token=0  # Old token
        )
        result = {
            "success": True, "duration_ms": 100,
            "output": "ok", "error": None,
        }
        future = MagicMock()
        future.result.return_value = result

        handle_attempt_completion(stale_attempt, future, "worker-a")
        attempt.refresh_from_db()
        # Should still be RUNNING (stale update rejected)
        assert attempt.status == JobAttempt.Status.RUNNING

    def test_failed_update_with_correct_token(self, job, worker_a):
        """Worker can mark attempt as FAILED with matching token."""
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            fencing_token=1,
        )
        result = {
            "success": False, "duration_ms": 50,
            "output": "", "error": "handler failed",
        }
        future = MagicMock()
        future.result.return_value = result

        handle_attempt_completion(attempt, future, "worker-a")
        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.FAILED

    def test_failed_update_rejected_with_wrong_token(self, job, worker_a):
        """Stale worker cannot mark attempt as FAILED."""
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            fencing_token=1,
        )
        stale_attempt = JobAttempt(
            id=attempt.id, fencing_token=0
        )
        result = {
            "success": False, "duration_ms": 50,
            "output": "", "error": "handler failed",
        }
        future = MagicMock()
        future.result.return_value = result

        handle_attempt_completion(stale_attempt, future, "worker-a")
        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.RUNNING


@pytest.mark.django_db
class TestStaleWorkerAfterWatchdogRetry:
    def test_old_worker_cannot_update_after_retry_created(
        self, job, worker_a
    ):
        """
        Full crash recovery scenario:
        1. Worker A claims attempt (token=1)
        2. Worker A crashes (lease expires)
        3. Watchdog creates retry (token=0)
        4. Worker B claims retry (token=1)
        5. Worker A wakes up, tries to update old attempt → REJECTED
        """
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        old_attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
            fencing_token=1,
        )

        # Watchdog detects expired lease, creates retry
        result = run_watchdog()
        assert result["rescheduled"] == 1

        retry = JobAttempt.objects.filter(job_run=run).exclude(
            id=old_attempt.id
        ).first()
        assert retry.fencing_token == 0
        assert retry.status == JobAttempt.Status.QUEUED

        # Worker B claims retry (token becomes 1)
        claimed = claim_attempt("worker-b")
        assert claimed.id == retry.id
        assert claimed.fencing_token == 1

        # Worker A wakes up, tries to update old attempt with stale token
        stale_attempt = JobAttempt(
            id=old_attempt.id, fencing_token=0  # Old token
        )
        completion_result = {
            "success": True, "duration_ms": 200,
            "output": "late result", "error": None,
        }
        future = MagicMock()
        future.result.return_value = completion_result

        handle_attempt_completion(stale_attempt, future, "worker-a")

        # Old attempt should still be TIMED_OUT (watchdog marked it)
        old_attempt.refresh_from_db()
        assert old_attempt.status == JobAttempt.Status.TIMED_OUT

        # Retry should still be RUNNING (claimed by worker B)
        retry.refresh_from_db()
        assert retry.status == JobAttempt.Status.RUNNING
        assert retry.worker_id == "worker-b"


@pytest.mark.django_db
class TestConcurrentWatchdogAndWorker:
    def test_watchdog_does_not_touch_running_attempt_with_valid_lease(
        self, job, worker_a
    ):
        """Watchdog should not interfere with a healthy running attempt."""
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() + timedelta(seconds=30),
            fencing_token=1,
        )

        result = run_watchdog()
        assert result["expired_leases"] == 0
        assert result["rescheduled"] == 0

        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.RUNNING
        assert attempt.worker_id == "worker-a"

    def test_worker_claim_during_watchdog_expiry(self, job, worker_a, worker_b):
        """
        Race: Watchdog marks attempt TIMED_OUT while Worker B tries to claim.
        The claim uses SELECT FOR UPDATE SKIP LOCKED, so Worker B should
        either get the old attempt (if watchdog hasn't locked it yet) or
        get nothing (if watchdog already changed status).
        """
        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        attempt = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
            fencing_token=1,
        )

        # Both run concurrently in the DB
        run_watchdog()

        # After watchdog, attempt is TIMED_OUT — claim should find nothing
        # (or the new retry if one was created)
        claimed = claim_attempt("worker-b")
        if claimed is not None:
            # If something was claimed, it's the new retry, not the old one
            assert claimed.id != attempt.id
            assert claimed.fencing_token == 1

        # Old attempt is definitely TIMED_OUT
        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.TIMED_OUT


@pytest.mark.django_db
class TestRetryExhaustion:
    def test_marked_permanently_failed_after_retries_exhausted(
        self, job, worker_a
    ):
        """After all retries exhausted, run is PERMANENTLY_FAILED."""
        job.retry_limit = 2
        job.save(update_fields=["retry_limit"])

        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )

        # First attempt — expired
        a1 = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
            fencing_token=1,
        )

        # Watchdog creates retry (attempt 2)
        run_watchdog()
        assert JobAttempt.objects.filter(job_run=run).count() == 2

        # Second attempt — also expires
        a2 = JobAttempt.objects.filter(job_run=run).exclude(
            id=a1.id
        ).first()
        a2.status = JobAttempt.Status.RUNNING
        a2.worker_id = "worker-a"
        a2.lease_expires_at = tz.now() - timedelta(seconds=10)
        a2.fencing_token = 1
        a2.save()

        # Watchdog — retries exhausted, no new attempt
        run_watchdog()
        assert JobAttempt.objects.filter(job_run=run).count() == 2

        # Run is PERMANENTLY_FAILED
        run.refresh_from_db()
        assert run.status == JobRun.Status.PERMANENTLY_FAILED

    def test_worker_cannot_claim_after_retry_exhausted(self, job, worker_a):
        """Worker cannot claim if run is permanently failed."""
        job.retry_limit = 1
        job.save(update_fields=["retry_limit"])

        run = JobRun.objects.create(
            job=job, scheduled_for=tz.now(), status=JobRun.Status.RUNNING
        )
        a1 = JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH", worker_id="worker-a",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
            fencing_token=1,
        )

        run_watchdog()
        run.refresh_from_db()
        assert run.status == JobRun.Status.PERMANENTLY_FAILED

        # No more claims possible for this run
        claimed = claim_attempt("worker-b")
        if claimed is not None:
            assert claimed.job_run_id != run.id


@pytest.mark.django_db
class TestFencingTokenEndToEnd:
    def test_complete_fencing_flow(self, job, worker_a, worker_b):
        """
        Full lifecycle test:
        1. Scheduler creates run + attempt (token=0)
        2. Worker A claims (token=1)
        3. Worker A crashes
        4. Watchdog creates retry (token=0)
        5. Worker B claims retry (token=1)
        6. Worker B completes → SUCCESS
        7. Worker A tries to update old attempt → REJECTED
        """
        # 1. Scheduler creates
        tick()
        run = JobRun.objects.first()
        assert run is not None
        attempt = JobAttempt.objects.filter(job_run=run).first()
        assert attempt.fencing_token == 0

        # 2. Worker A claims
        claimed_a = claim_attempt("worker-a")
        assert claimed_a.fencing_token == 1
        assert claimed_a.worker_id == "worker-a"

        # 3. Worker A crashes (lease expires)
        claimed_a.lease_expires_at = tz.now() - timedelta(seconds=10)
        claimed_a.save(update_fields=["lease_expires_at"])

        # 4. Watchdog creates retry
        run_watchdog()
        retry = JobAttempt.objects.filter(job_run=run).exclude(
            id=attempt.id
        ).first()
        assert retry is not None
        assert retry.fencing_token == 0

        # 5. Worker B claims retry
        claimed_b = claim_attempt("worker-b")
        assert claimed_b.id == retry.id
        assert claimed_b.fencing_token == 1

        # 6. Worker B completes successfully
        result = {
            "success": True, "duration_ms": 100,
            "output": "done", "error": None,
        }
        future = MagicMock()
        future.result.return_value = result
        handle_attempt_completion(claimed_b, future, "worker-b")

        claimed_b.refresh_from_db()
        assert claimed_b.status == JobAttempt.Status.SUCCESS

        # 7. Worker A tries to update old attempt (stale)
        stale = JobAttempt(id=attempt.id, fencing_token=0)
        future2 = MagicMock()
        future2.result.return_value = result
        handle_attempt_completion(stale, future2, "worker-a")

        attempt.refresh_from_db()
        # Old attempt is TIMED_OUT, not affected by stale update
        assert attempt.status == JobAttempt.Status.TIMED_OUT
