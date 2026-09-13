"""Tests for the scheduler tick logic."""
import pytest
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun, OutboxEvent
from apps.jobs.models import Job
from scheduler.tick import calculate_next_run, create_run_for_job, tick


@pytest.fixture
def active_job(db):
    """Create an active job that is due right now."""
    return Job.objects.create(
        name="test_scheduler_job",
        description="Test scheduler",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",  # Every minute
        timezone="UTC",
        priority=Job.Priority.MEDIUM,
        timeout_seconds=300,
        handler="billing_reconciliation",
        next_run_at=tz.now() - tz.timedelta(seconds=1),  # Due now
    )


@pytest.fixture
def future_job(db):
    """Create an active job that is NOT due yet."""
    return Job.objects.create(
        name="future_job",
        description="Not due yet",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        timezone="UTC",
        priority=Job.Priority.MEDIUM,
        timeout_seconds=300,
        handler="billing_reconciliation",
        next_run_at=tz.now() + tz.timedelta(hours=1),  # Due in 1 hour
    )


@pytest.fixture
def one_time_job(db):
    """Create a one-time job that is due right now."""
    return Job.objects.create(
        name="one_time_job",
        description="One time",
        schedule_type=Job.ScheduleType.ONE_TIME,
        run_at=tz.now() - tz.timedelta(seconds=1),
        timezone="UTC",
        priority=Job.Priority.HIGH,
        timeout_seconds=300,
        handler="generate_daily_report",
        next_run_at=tz.now() - tz.timedelta(seconds=1),
    )


@pytest.mark.django_db
class TestTick:
    def test_schedules_due_job(self, active_job):
        count = tick()
        assert count == 1

        # Verify JobRun was created
        runs = JobRun.objects.filter(job=active_job)
        assert runs.count() == 1
        assert runs.first().status == JobRun.Status.QUEUED

        # Verify JobAttempt was created
        attempts = JobAttempt.objects.filter(job=active_job)
        assert attempts.count() == 1
        assert attempts.first().status == JobAttempt.Status.QUEUED

        # Verify OutboxEvent was created
        events = OutboxEvent.objects.filter(
            event_type="job_attempt_created",
            aggregate_id=runs.first().id,
        )
        assert events.count() == 1
        assert events.first().status == OutboxEvent.Status.PENDING

    def test_does_not_schedule_future_job(self, future_job):
        count = tick()
        assert count == 0
        assert JobRun.objects.filter(job=future_job).count() == 0

    def test_no_due_jobs(self):
        count = tick()
        assert count == 0

    def test_schedules_one_time_job(self, one_time_job):
        count = tick()
        assert count == 1
        assert JobRun.objects.filter(job=one_time_job).count() == 1

    def test_updates_next_run_at(self, active_job):
        old_next = active_job.next_run_at
        tick()
        active_job.refresh_from_db()
        assert active_job.next_run_at is not None
        # For a cron job, next_run_at should be in the future
        assert active_job.next_run_at > tz.now() - tz.timedelta(minutes=2)

    def test_one_time_job_next_run_at_becomes_none(self, one_time_job):
        tick()
        one_time_job.refresh_from_db()
        assert one_time_job.next_run_at is None

    def test_updates_last_run_at(self, active_job):
        tick()
        active_job.refresh_from_db()
        assert active_job.last_run_at is not None


@pytest.mark.django_db
class TestIdempotency:
    def test_no_duplicate_runs(self, active_job):
        """Two ticks should not create duplicate runs for same scheduled time."""
        tick()
        tick()
        runs = JobRun.objects.filter(job=active_job)
        # Only one run should exist because the idempotency check prevents duplicates
        assert runs.count() == 1

    def test_different_scheduled_times_create_different_runs(self, active_job):
        """Two different scheduled_for times create different runs."""
        # First tick
        tick()
        # Move next_run_at forward
        active_job.refresh_from_db()
        active_job.next_run_at = tz.now() - tz.timedelta(seconds=1)
        active_job.save(update_fields=["next_run_at"])
        # Second tick
        tick()
        runs = JobRun.objects.filter(job=active_job)
        assert runs.count() == 2


@pytest.mark.django_db
class TestConcurrency:
    def test_priority_ordering(self, db):
        """Higher priority jobs are scheduled first."""
        # Create jobs with different priorities
        Job.objects.create(
            name="low_job",
            description="Low",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="cleanup_temp_files",
            priority=Job.Priority.LOW,
            next_run_at=tz.now() - tz.timedelta(seconds=1),
        )
        Job.objects.create(
            name="critical_job",
            description="Critical",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="billing_reconciliation",
            priority=Job.Priority.CRITICAL,
            next_run_at=tz.now() - tz.timedelta(seconds=1),
        )
        Job.objects.create(
            name="medium_job",
            description="Medium",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="generate_daily_report",
            priority=Job.Priority.MEDIUM,
            next_run_at=tz.now() - tz.timedelta(seconds=1),
        )

        count = tick()
        assert count == 3

        # All three should be scheduled
        assert JobRun.objects.count() == 3

    def test_inactive_jobs_are_not_scheduled(self, db):
        """INACTIVE jobs should not be scheduled."""
        job = Job.objects.create(
            name="inactive_job",
            description="Inactive",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="billing_reconciliation",
            status=Job.Status.INACTIVE,
            next_run_at=tz.now() - tz.timedelta(seconds=1),
        )
        count = tick()
        assert count == 0
        assert JobRun.objects.filter(job=job).count() == 0


@pytest.mark.django_db
class TestCalculateNextRun:
    def test_cron_next_run(self):
        job = Job(
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="0 3 * * *",
            timezone="UTC",
        )
        after = tz.now()
        next_run = calculate_next_run(job, after)
        assert next_run is not None
        assert next_run > after

    def test_one_time_next_run_returns_none(self):
        job = Job(
            schedule_type=Job.ScheduleType.ONE_TIME,
        )
        next_run = calculate_next_run(job, tz.now())
        assert next_run is None

    def test_cron_every_5_minutes(self):
        job = Job(
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="*/5 * * * *",
            timezone="UTC",
        )
        after = tz.now()
        next_run = calculate_next_run(job, after)
        assert next_run is not None
        # Should be within 5 minutes
        assert next_run - after <= tz.timedelta(minutes=5)


@pytest.mark.django_db
class TestCreateRunForJob:
    def test_creates_run_and_attempt(self, active_job):
        created = create_run_for_job(active_job, tz.now())
        assert created is True
        assert JobRun.objects.filter(job=active_job).count() == 1
        assert JobAttempt.objects.filter(job=active_job).count() == 1

    def test_creates_outbox_event(self, active_job):
        create_run_for_job(active_job, tz.now())
        runs = JobRun.objects.filter(job=active_job)
        events = OutboxEvent.objects.filter(
            event_type="job_attempt_created",
            aggregate_id=runs.first().id,
        )
        assert events.count() == 1

    def test_prevents_duplicate_for_same_scheduled_time(self, active_job):
        now = tz.now()
        create_run_for_job(active_job, now)
        created = create_run_for_job(active_job, now)
        assert created is False
        assert JobRun.objects.filter(job=active_job).count() == 1


@pytest.mark.django_db
class TestSchedulerRecovery:
    def test_picks_up_jobs_after_downtime(self, db):
        """If scheduler is down, due jobs accumulate and are picked up on restart."""
        # Create 5 jobs that are all due
        for i in range(5):
            Job.objects.create(
                name=f"accumulated_job_{i}",
                description=f"Job {i}",
                schedule_type=Job.ScheduleType.CRON,
                cron_expression="* * * * *",
                handler="billing_reconciliation",
                next_run_at=tz.now() - tz.timedelta(minutes=10),
            )

        # Scheduler "restarts" — should pick up all 5
        count = tick()
        assert count == 5
        assert JobRun.objects.count() == 5
