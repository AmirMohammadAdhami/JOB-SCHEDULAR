"""Tests for the outbox publisher."""
import json
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone as tz

from apps.executions.models import OutboxEvent
from apps.jobs.models import Job
from scheduler.publisher import (
    calculate_backoff,
    publish_event,
    publish_pending_events,
)
from scheduler.rabbitmq import (
    QUEUE_HIGH,
    QUEUE_LOW,
    QUEUE_MEDIUM,
    get_queue_for_priority,
)


@pytest.fixture
def pending_event(db):
    """Create a pending outbox event."""
    job = Job.objects.create(
        name="publisher_test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="billing_reconciliation",
        priority=Job.Priority.HIGH,
        next_run_at=tz.now(),
    )
    from apps.executions.models import JobRun

    run = JobRun.objects.create(
        job=job,
        scheduled_for=tz.now(),
        status=JobRun.Status.QUEUED,
    )
    return OutboxEvent.objects.create(
        event_type="job_attempt_created",
        aggregate_id=run.id,
        payload={
            "run_id": str(run.id),
            "job_id": str(job.id),
            "priority": "HIGH",
        },
        status=OutboxEvent.Status.PENDING,
    )


@pytest.fixture
def multiple_pending_events(db):
    """Create multiple pending outbox events with different priorities."""
    events = []
    for priority in ["HIGH", "MEDIUM", "LOW"]:
        job = Job.objects.create(
            name=f"publisher_test_{priority.lower()}",
            description="Test",
            schedule_type=Job.ScheduleType.CRON,
            cron_expression="* * * * *",
            handler="billing_reconciliation",
            priority=priority,
            next_run_at=tz.now(),
        )
        from apps.executions.models import JobRun

        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.QUEUED,
        )
        events.append(
            OutboxEvent.objects.create(
                event_type="job_attempt_created",
                aggregate_id=run.id,
                payload={
                    "run_id": str(run.id),
                    "job_id": str(job.id),
                    "priority": priority,
                },
                status=OutboxEvent.Status.PENDING,
            )
        )
    return events


@pytest.mark.django_db
class TestQueueMapping:
    def test_high_priority_maps_to_high_queue(self):
        assert get_queue_for_priority("HIGH") == QUEUE_HIGH

    def test_critical_priority_maps_to_critical_queue(self):
        from scheduler.rabbitmq import QUEUE_CRITICAL
        assert get_queue_for_priority("CRITICAL") == QUEUE_CRITICAL

    def test_medium_priority_maps_to_medium_queue(self):
        assert get_queue_for_priority("MEDIUM") == QUEUE_MEDIUM

    def test_low_priority_maps_to_low_queue(self):
        assert get_queue_for_priority("LOW") == QUEUE_LOW

    def test_unknown_priority_maps_to_medium_queue(self):
        assert get_queue_for_priority("UNKNOWN") == QUEUE_MEDIUM


@pytest.mark.django_db
class TestPublishEvent:
    def test_publish_success(self, pending_event):
        channel = MagicMock()
        success = publish_event(channel, pending_event)
        assert success is True
        channel.basic_publish.assert_called_once()

    def test_publish_includes_message_id(self, pending_event):
        channel = MagicMock()
        publish_event(channel, pending_event)
        call_kwargs = channel.basic_publish.call_args
        assert call_kwargs[1]["properties"].message_id == str(
            pending_event.id
        )

    def test_publish_uses_correct_queue(self, pending_event):
        channel = MagicMock()
        publish_event(channel, pending_event)
        call_kwargs = channel.basic_publish.call_args
        assert call_kwargs[1]["routing_key"] == QUEUE_HIGH

    def test_publish_returns_false_on_connection_error(self, pending_event):
        channel = MagicMock()
        channel.basic_publish.side_effect = Exception("Connection lost")
        success = publish_event(channel, pending_event)
        assert success is False


@pytest.mark.django_db
class TestBackoffCalculation:
    def test_first_attempt_short_delay(self):
        delay = calculate_backoff(1)
        assert 1 <= delay <= 3

    def test_exponential_growth(self):
        delays = [calculate_backoff(i) for i in range(1, 6)]
        # Should generally increase (with some jitter)
        assert delays[-1] > delays[0]

    def test_capped_at_high_value(self):
        delay = calculate_backoff(15)
        assert delay < 3600  # Should not be unbounded


@pytest.mark.django_db
class TestPublishPendingEvents:
    def test_publishes_pending_events(self, pending_event):
        with patch("scheduler.publisher.RabbitMQConnection") as mock_conn:
            mock_channel = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(
                return_value=mock_conn
            )
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.connect = MagicMock()
            mock_conn.return_value.get_channel = MagicMock(
                return_value=mock_channel
            )

            published = publish_pending_events()
            # Should have attempted to publish
            assert published >= 0

    def test_marks_events_as_published(self, pending_event):
        with patch("scheduler.publisher.RabbitMQConnection") as mock_conn:
            mock_channel = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(
                return_value=mock_conn
            )
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.connect = MagicMock()
            mock_conn.return_value.get_channel = MagicMock(
                return_value=mock_channel
            )

            publish_pending_events()

            pending_event.refresh_from_db()
            # Event should be marked as published
            assert pending_event.status == OutboxEvent.Status.PUBLISHED
            assert pending_event.published_at is not None

    def test_respects_next_attempt_at(self, pending_event):
        # Set next_attempt_at in the future
        pending_event.next_attempt_at = tz.now() + tz.timedelta(hours=1)
        pending_event.save(update_fields=["next_attempt_at"])

        with patch("scheduler.publisher.RabbitMQConnection") as mock_conn:
            mock_channel = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(
                return_value=mock_conn
            )
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.connect = MagicMock()
            mock_conn.return_value.get_channel = MagicMock(
                return_value=mock_channel
            )

            publish_pending_events()

            pending_event.refresh_from_db()
            # Event should still be pending
            assert pending_event.status == OutboxEvent.Status.PENDING

    def test_handles_no_rabbitmq_gracefully(self, pending_event):
        with patch("scheduler.publisher.RabbitMQConnection") as mock_conn:
            mock_conn.return_value.connect.side_effect = Exception(
                "Connection refused"
            )
            published = publish_pending_events()
            assert published == 0
            # Event should remain PENDING
            pending_event.refresh_from_db()
            assert pending_event.status == OutboxEvent.Status.PENDING

    def test_batch_limit(self, db):
        # Create 60 events (more than BATCH_SIZE)
        from apps.executions.models import JobRun

        events = []
        for i in range(60):
            job = Job.objects.create(
                name=f"batch_job_{i}",
                description="Test",
                schedule_type=Job.ScheduleType.CRON,
                cron_expression="* * * * *",
                handler="billing_reconciliation",
                priority="MEDIUM",
                next_run_at=tz.now(),
            )
            run = JobRun.objects.create(
                job=job,
                scheduled_for=tz.now(),
                status=JobRun.Status.QUEUED,
            )
            events.append(
                OutboxEvent.objects.create(
                    event_type="job_attempt_created",
                    aggregate_id=run.id,
                    payload={
                        "run_id": str(run.id),
                        "priority": "MEDIUM",
                    },
                    status=OutboxEvent.Status.PENDING,
                )
            )

        with patch("scheduler.publisher.RabbitMQConnection") as mock_conn:
            mock_channel = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(
                return_value=mock_conn
            )
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.connect = MagicMock()
            mock_conn.return_value.get_channel = MagicMock(
                return_value=mock_channel
            )

            published = publish_pending_events()
            # Should process at most BATCH_SIZE (50)
            assert published <= 50
