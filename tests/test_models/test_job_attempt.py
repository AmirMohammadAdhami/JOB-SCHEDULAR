"""Tests for the JobAttempt model."""
import pytest
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun
from apps.jobs.models import Job


@pytest.fixture
def job_and_run(db):
    job = Job.objects.create(
        name="test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="test_handler",
        priority=Job.Priority.MEDIUM,
    )
    run = JobRun.objects.create(
        job=job,
        scheduled_for=tz.now(),
    )
    return job, run


@pytest.mark.django_db
class TestJobAttemptModel:
    def test_create_attempt(self, job_and_run):
        job, run = job_and_run
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            priority=job.priority,
            scheduled_for=tz.now(),
        )
        assert attempt.id is not None
        assert attempt.status == JobAttempt.Status.QUEUED
        assert attempt.fencing_token == 0
        assert attempt.worker_id is None
        assert attempt.lease_expires_at is None

    def test_claim_attempt(self, job_and_run):
        job, run = job_and_run
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            priority=job.priority,
            scheduled_for=tz.now(),
        )
        # Simulate claim: atomic UPDATE WHERE status='QUEUED'
        updated = JobAttempt.objects.filter(
            id=attempt.id, status=JobAttempt.Status.QUEUED
        ).update(
            status=JobAttempt.Status.RUNNING,
            worker_id="worker-01.pid-12345",
            fencing_token=attempt.fencing_token + 1,
            started_at=tz.now(),
            lease_expires_at=tz.now() + tz.timedelta(seconds=45),
        )
        assert updated == 1

        attempt.refresh_from_db()
        assert attempt.status == JobAttempt.Status.RUNNING
        assert attempt.worker_id == "worker-01.pid-12345"
        assert attempt.fencing_token == 1

    def test_double_claim_fails(self, job_and_run):
        """Two workers try to claim the same attempt — only one succeeds."""
        job, run = job_and_run
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            priority=job.priority,
            scheduled_for=tz.now(),
        )

        # Worker A claims
        updated_a = JobAttempt.objects.filter(
            id=attempt.id, status=JobAttempt.Status.QUEUED
        ).update(
            status=JobAttempt.Status.RUNNING,
            worker_id="worker-a",
            fencing_token=1,
        )
        assert updated_a == 1

        # Worker B tries to claim — should fail (0 rows updated)
        updated_b = JobAttempt.objects.filter(
            id=attempt.id, status=JobAttempt.Status.QUEUED
        ).update(
            status=JobAttempt.Status.RUNNING,
            worker_id="worker-b",
            fencing_token=1,
        )
        assert updated_b == 0

    def test_fencing_token_prevents_stale_update(self, job_and_run):
        """Stale worker with old fencing token cannot update."""
        job, run = job_and_run
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            priority=job.priority,
            scheduled_for=tz.now(),
        )

        # Worker A claims (fencing_token=1)
        JobAttempt.objects.filter(
            id=attempt.id, status=JobAttempt.Status.QUEUED
        ).update(
            status=JobAttempt.Status.RUNNING,
            worker_id="worker-a",
            fencing_token=1,
        )

        # Watchdog marks as TIMED_OUT, Worker B claims (fencing_token=2)
        JobAttempt.objects.filter(id=attempt.id).update(
            status=JobAttempt.Status.TIMED_OUT,
        )
        JobAttempt.objects.filter(id=attempt.id).update(
            status=JobAttempt.Status.QUEUED,
            attempt_number=2,
        )
        JobAttempt.objects.filter(
            id=attempt.id, status=JobAttempt.Status.QUEUED
        ).update(
            status=JobAttempt.Status.RUNNING,
            worker_id="worker-b",
            fencing_token=2,
        )

        # Worker A tries to update with old fencing_token=1 — should fail
        stale_update = JobAttempt.objects.filter(
            id=attempt.id, fencing_token=1
        ).update(status=JobAttempt.Status.SUCCESS)
        assert stale_update == 0

        # Worker B succeeds with current fencing_token=2
        current_update = JobAttempt.objects.filter(
            id=attempt.id, fencing_token=2
        ).update(status=JobAttempt.Status.SUCCESS)
        assert current_update == 1

    def test_lease_renewal(self, job_and_run):
        job, run = job_and_run
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            priority=job.priority,
            scheduled_for=tz.now(),
            status=JobAttempt.Status.RUNNING,
            worker_id="worker-01",
            lease_expires_at=tz.now(),
        )

        new_expiry = tz.now() + tz.timedelta(seconds=45)
        JobAttempt.objects.filter(
            id=attempt.id,
            worker_id="worker-01",
            status=JobAttempt.Status.RUNNING,
        ).update(lease_expires_at=new_expiry)

        attempt.refresh_from_db()
        assert attempt.lease_expires_at > tz.now() - tz.timedelta(seconds=5)

    def test_attempt_number_increments(self, job_and_run):
        job, run = job_and_run
        for i in range(1, 4):
            attempt = JobAttempt.objects.create(
                job_run=run,
                job=job,
                attempt_number=i,
                priority=job.priority,
                scheduled_for=tz.now(),
            )
            assert attempt.attempt_number == i

    def test_denormalized_job_id(self, job_and_run):
        job, run = job_and_run
        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            priority=job.priority,
            scheduled_for=tz.now(),
        )
        # Query by job_id directly (no JOIN through job_run)
        attempts = JobAttempt.objects.filter(job_id=job.id)
        assert attempts.count() == 1
