"""Tests for the scheduler watchdog."""
import pytest
from datetime import timedelta

from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun, Worker
from apps.jobs.models import Job
from scheduler.watchdog import (
    run_watchdog,
    _find_expired_leases,
    _handle_expired_attempt,
    _mark_permanently_failed,
    _mark_dead_workers,
)


@pytest.fixture
def job(db):
    return Job.objects.create(
        name="watchdog_test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="billing_reconciliation",
        priority=Job.Priority.HIGH,
        retry_limit=3,
        next_run_at=tz.now(),
    )


@pytest.fixture
def run(job):
    return JobRun.objects.create(
        job=job,
        scheduled_for=tz.now(),
        status=JobRun.Status.RUNNING,
    )


@pytest.fixture
def running_attempt(job, run):
    return JobAttempt.objects.create(
        job_run=run,
        job=job,
        attempt_number=1,
        status=JobAttempt.Status.RUNNING,
        priority="HIGH",
        worker_id="crashed-worker",
        fencing_token=1,
        scheduled_for=tz.now(),
        started_at=tz.now(),
        lease_expires_at=tz.now() - timedelta(seconds=10),  # Expired
    )


@pytest.fixture
def active_worker(db):
    return Worker.objects.create(
        id="active-worker",
        hostname="host1",
        status=Worker.Status.ACTIVE,
        last_heartbeat_at=tz.now(),
    )


@pytest.fixture
def dead_worker(db):
    return Worker.objects.create(
        id="dead-worker",
        hostname="host2",
        status=Worker.Status.ACTIVE,
        last_heartbeat_at=tz.now() - timedelta(seconds=120),
    )


@pytest.mark.django_db
class TestExpiredLeases:
    def test_find_expired_leases(self, running_attempt):
        expired = _find_expired_leases(tz.now())
        assert len(expired) == 1
        assert expired[0].id == running_attempt.id

    def test_no_expired_when_lease_valid(self, job, run):
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH",
            worker_id="alive-worker",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() + timedelta(seconds=30),
        )
        expired = _find_expired_leases(tz.now())
        assert len(expired) == 0

    def test_expired_only_for_running(self, job, run):
        """Only RUNNING attempts with expired leases are found."""
        JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
        )
        expired = _find_expired_leases(tz.now())
        assert len(expired) == 0


@pytest.mark.django_db
class TestHandleExpiredAttempt:
    def test_marks_as_timed_out(self, running_attempt):
        _handle_expired_attempt(running_attempt, tz.now())
        running_attempt.refresh_from_db()
        assert running_attempt.status == JobAttempt.Status.TIMED_OUT
        assert running_attempt.error_message is not None

    def test_creates_new_attempt_if_retries_left(self, running_attempt, run):
        _handle_expired_attempt(running_attempt, tz.now())
        attempts = JobAttempt.objects.filter(job_run=run)
        assert attempts.count() == 2
        new_attempt = attempts.exclude(id=running_attempt.id).first()
        assert new_attempt.status == JobAttempt.Status.QUEUED
        assert new_attempt.attempt_number == 2

    def test_no_new_attempt_if_retries_exhausted(self, job, run):
        """When retry_limit is reached, no new attempt is created."""
        job.retry_limit = 1
        job.save(update_fields=["retry_limit"])

        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH",
            worker_id="crashed",
            scheduled_for=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
        )
        _handle_expired_attempt(attempt, tz.now())
        assert JobAttempt.objects.filter(job_run=run).count() == 1

    def test_handles_already_claimed_attempt(self, running_attempt, active_worker):
        """If another worker already claimed it, don't reschedule."""
        # Simulate another worker claiming it
        running_attempt.status = JobAttempt.Status.RUNNING
        running_attempt.worker_id = "new-worker"
        running_attempt.lease_expires_at = tz.now() + timedelta(seconds=30)
        running_attempt.save()

        result = _handle_expired_attempt(running_attempt, tz.now())
        assert result is False


@pytest.mark.django_db
class TestMarkPermanentlyFailed:
    def test_marks_run_when_all_attempts_failed(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.RUNNING,
        )
        JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.FAILED,
            priority="HIGH", scheduled_for=tz.now(),
        )
        count = _mark_permanently_failed(tz.now())
        assert count == 1
        run.refresh_from_db()
        assert run.status == JobRun.Status.PERMANENTLY_FAILED

    def test_does_not_mark_if_attempt_succeeded(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.RUNNING,
        )
        JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.SUCCESS,
            priority="HIGH", scheduled_for=tz.now(),
        )
        count = _mark_permanently_failed(tz.now())
        # Run gets marked as SUCCESS (not permanently_failed)
        assert count == 1
        run.refresh_from_db()
        assert run.status == JobRun.Status.SUCCESS

    def test_skips_if_active_attempts_exist(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.RUNNING,
        )
        JobAttempt.objects.create(
            job_run=run, job=job, attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH", scheduled_for=tz.now(),
        )
        count = _mark_permanently_failed(tz.now())
        assert count == 0


@pytest.mark.django_db
class TestMarkDeadWorkers:
    def test_marks_dead_workers(self, dead_worker):
        count = _mark_dead_workers(tz.now())
        assert count == 1
        dead_worker.refresh_from_db()
        assert dead_worker.status == Worker.Status.DEAD

    def test_does_not_mark_active_workers(self, active_worker):
        count = _mark_dead_workers(tz.now())
        assert count == 0
        active_worker.refresh_from_db()
        assert active_worker.status == Worker.Status.ACTIVE


@pytest.mark.django_db
class TestRunWatchdog:
    def test_returns_zero_when_nothing_to_do(self, active_worker):
        result = run_watchdog()
        assert result["expired_leases"] == 0
        assert result["rescheduled"] == 0
        assert result["dead_workers"] == 0

    def test_handles_expired_and_dead(self, running_attempt, dead_worker):
        result = run_watchdog()
        assert result["expired_leases"] == 1
        assert result["rescheduled"] == 1
        assert result["dead_workers"] == 1

    def test_full_crash_recovery_flow(self, job, active_worker):
        """Simulate a worker crash and verify full recovery."""
        # Worker starts a job
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.RUNNING,
        )
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH",
            worker_id="crashing-worker",
            scheduled_for=tz.now(),
            started_at=tz.now(),
            lease_expires_at=tz.now() - timedelta(seconds=10),
        )

        # Worker crashes (lease expires)

        # Watchdog runs
        result = run_watchdog()
        assert result["expired_leases"] == 1
        assert result["rescheduled"] == 1

        # Verify new attempt exists
        attempts = JobAttempt.objects.filter(job_run=run)
        assert attempts.count() == 2
        new_attempt = attempts.exclude(id=attempt.id).first()
        assert new_attempt.status == JobAttempt.Status.QUEUED
        assert new_attempt.attempt_number == 2

        # Original attempt is marked as TIMED_OUT
        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.TIMED_OUT
