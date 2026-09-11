"""Tests for the Job model."""
import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone as tz

from apps.jobs.models import Job


@pytest.mark.django_db
class TestJobModel:
    def test_create_cron_job(self):
        job = Job.objects.create(
            name="test_cron_job",
            description="A test cron job",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="0 3 * * *",
            timezone="UTC",
            priority=Job.Priority.MEDIUM,
            timeout_seconds=300,
            handler="test_handler",
            retry_limit=3,
            retry_backoff_type=Job.BackoffType.EXPONENTIAL,
            retry_base_delay_seconds=30,
        )
        assert job.id is not None
        assert job.name == "test_cron_job"
        assert job.status == Job.Status.ACTIVE
        assert job.schedule_type == Job.ScheduleType.CRON

    def test_create_one_time_job(self):
        run_at = tz.now() + tz.timedelta(hours=1)
        job = Job.objects.create(
            name="test_one_time",
            description="A one-time job",
            schedule_type=Job.ScheduleType.ONE_TIME,
            run_at=run_at,
            priority=Job.Priority.HIGH,
            timeout_seconds=600,
            handler="test_handler",
        )
        assert job.schedule_type == Job.ScheduleType.ONE_TIME
        assert job.run_at == run_at

    def test_name_uniqueness(self):
        Job.objects.create(
            name="unique_name",
            description="First job",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="test_handler",
        )
        with pytest.raises(Exception):
            Job.objects.create(
                name="unique_name",
                description="Duplicate name",
                schedule_type=Job.ScheduleType.CRON,
                cron_expression="* * * * *",
                handler="test_handler",
            )

    def test_cron_requires_cron_expression(self):
        job = Job(
            name="cron_no_expr",
            description="Missing cron expression",
            schedule_type=Job.ScheduleType.CRON,
            handler="test_handler",
        )
        with pytest.raises(ValidationError) as exc_info:
            job.full_clean()
        assert "cron_expression" in exc_info.value.message_dict

    def test_one_time_requires_run_at(self):
        job = Job(
            name="one_time_no_run_at",
            description="Missing run_at",
            schedule_type=Job.ScheduleType.ONE_TIME,
            handler="test_handler",
        )
        with pytest.raises(ValidationError) as exc_info:
            job.full_clean()
        assert "run_at" in exc_info.value.message_dict

    def test_priority_choices(self):
        for priority in Job.Priority:
            job = Job(
                name=f"job_{priority.value.lower()}",
                description=f"Test {priority}",
                schedule_type=Job.ScheduleType.CRON,
                cron_expression="* * * * *",
                handler="test_handler",
                priority=priority,
            )
            job.full_clean()  # Should not raise

    def test_timeout_bounds(self):
        job = Job(
            name="timeout_too_low",
            description="Timeout below minimum",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="test_handler",
            timeout_seconds=5,
        )
        with pytest.raises(ValidationError) as exc_info:
            job.full_clean()
        assert "timeout_seconds" in exc_info.value.message_dict

    def test_retry_limit_bounds(self):
        job = Job(
            name="retry_too_high",
            description="Retry limit above maximum",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="test_handler",
            retry_limit=15,
        )
        with pytest.raises(ValidationError) as exc_info:
            job.full_clean()
        assert "retry_limit" in exc_info.value.message_dict

    def test_soft_delete(self):
        job = Job.objects.create(
            name="to_delete",
            description="Will be deleted",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="test_handler",
        )
        job.status = Job.Status.DELETED
        job.save()
        job.refresh_from_db()
        assert job.status == Job.Status.DELETED

    def test_default_values(self):
        job = Job.objects.create(
            name="defaults_test",
            description="Testing defaults",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="test_handler",
        )
        assert job.priority == Job.Priority.MEDIUM
        assert job.timeout_seconds == 300
        assert job.retry_limit == 1
        assert job.retry_backoff_type == Job.BackoffType.FIXED
        assert job.retry_base_delay_seconds == 30
        assert job.timezone == "UTC"
        assert job.status == Job.Status.ACTIVE
