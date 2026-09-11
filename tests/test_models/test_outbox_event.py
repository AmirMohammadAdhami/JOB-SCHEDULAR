"""Tests for the OutboxEvent model."""
import pytest
from django.utils import timezone as tz

from apps.executions.models import OutboxEvent


@pytest.mark.django_db
class TestOutboxEventModel:
    def test_create_event(self):
        event = OutboxEvent.objects.create(
            event_type="job_attempt_created",
            aggregate_id="12345678-1234-5678-1234-567812345678",
            payload={"attempt_id": "test", "priority": "HIGH"},
        )
        assert event.id is not None
        assert event.status == OutboxEvent.Status.PENDING
        assert event.attempts == 0

    def test_mark_published(self):
        event = OutboxEvent.objects.create(
            event_type="job_attempt_created",
            aggregate_id="12345678-1234-5678-1234-567812345678",
            payload={"attempt_id": "test"},
        )
        event.status = OutboxEvent.Status.PUBLISHED
        event.published_at = tz.now()
        event.save()
        event.refresh_from_db()
        assert event.status == OutboxEvent.Status.PUBLISHED
        assert event.published_at is not None

    def test_failed_publish(self):
        event = OutboxEvent.objects.create(
            event_type="job_attempt_created",
            aggregate_id="12345678-1234-5678-1234-567812345678",
            payload={"attempt_id": "test"},
        )
        event.attempts = 1
        event.status = OutboxEvent.Status.FAILED
        event.save()
        event.refresh_from_db()
        assert event.attempts == 1
        assert event.status == OutboxEvent.Status.FAILED

    def test_payload_is_json(self):
        complex_payload = {
            "attempt_id": "abc-123",
            "priority": "CRITICAL",
            "metadata": {"retry_count": 2},
        }
        event = OutboxEvent.objects.create(
            event_type="test_event",
            aggregate_id="12345678-1234-5678-1234-567812345678",
            payload=complex_payload,
        )
        event.refresh_from_db()
        assert event.payload["priority"] == "CRITICAL"
        assert event.payload["metadata"]["retry_count"] == 2
