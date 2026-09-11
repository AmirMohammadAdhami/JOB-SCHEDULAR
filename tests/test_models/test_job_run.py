"""Tests for the JobRun model."""
import pytest
from django.utils import timezone as tz

from apps.executions.models import JobRun
from apps.jobs.models import Job


@pytest.fixture
def job(db):
    return Job.objects.create(
        name="test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="test_handler",
    )


@pytest.mark.django_db
class TestJobRunModel:
    def test_create_job_run(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
        )
        assert run.id is not None
        assert run.status == JobRun.Status.QUEUED
        assert run.total_attempts == 1

    def test_status_transitions(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
        )
        # QUEUED -> RUNNING
        run.status = JobRun.Status.RUNNING
        run.started_at = tz.now()
        run.save()

        # RUNNING -> SUCCESS
        run.status = JobRun.Status.SUCCESS
        run.finished_at = tz.now()
        run.save()

        run.refresh_from_db()
        assert run.status == JobRun.Status.SUCCESS

    def test_permanently_failed(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.RUNNING,
        )
        run.status = JobRun.Status.PERMANENTLY_FAILED
        run.finished_at = tz.now()
        run.save()
        run.refresh_from_db()
        assert run.status == JobRun.Status.PERMANENTLY_FAILED

    def test_cancelled(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
        )
        run.status = JobRun.Status.CANCELLED
        run.save()
        run.refresh_from_db()
        assert run.status == JobRun.Status.CANCELLED

    def test_cascade_delete(self, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
        )
        run_id = run.id
        job.delete()
        assert not JobRun.objects.filter(id=run_id).exists()

    def test_ordering(self, job):
        run1 = JobRun.objects.create(
            job=job, scheduled_for=tz.now()
        )
        run2 = JobRun.objects.create(
            job=job, scheduled_for=tz.now()
        )
        runs = list(JobRun.objects.all())
        assert runs[0].id == run2.id  # Most recent first
        assert runs[1].id == run1.id
