"""Tests for the Worker model."""
import pytest
from django.utils import timezone as tz

from apps.executions.models import Worker


@pytest.mark.django_db
class TestWorkerModel:
    def test_register_worker(self):
        worker = Worker.objects.create(
            id="worker-01.pid-12345",
            hostname="worker-01",
        )
        assert worker.id == "worker-01.pid-12345"
        assert worker.status == Worker.Status.ACTIVE
        assert worker.last_heartbeat_at is not None

    def test_heartbeat_update(self):
        worker = Worker.objects.create(
            id="worker-01.pid-12345",
            hostname="worker-01",
        )
        old_heartbeat = worker.last_heartbeat_at

        worker.last_heartbeat_at = tz.now()
        worker.save()
        worker.refresh_from_db()
        assert worker.last_heartbeat_at > old_heartbeat

    def test_mark_dead(self):
        worker = Worker.objects.create(
            id="worker-01.pid-12345",
            hostname="worker-01",
        )
        worker.status = Worker.Status.DEAD
        worker.save()
        worker.refresh_from_db()
        assert worker.status == Worker.Status.DEAD

    def test_upsert_on_restart(self):
        """Worker re-registers after restart (ON CONFLICT DO UPDATE)."""
        Worker.objects.create(
            id="worker-01.pid-12345",
            hostname="worker-01",
            status=Worker.Status.DEAD,
        )

        # Simulate restart: re-create with same ID
        Worker.objects.update_or_create(
            id="worker-01.pid-12345",
            defaults={
                "hostname": "worker-01",
                "status": Worker.Status.ACTIVE,
                "last_heartbeat_at": tz.now(),
                "started_at": tz.now(),
            },
        )
        worker = Worker.objects.get(id="worker-01.pid-12345")
        assert worker.status == Worker.Status.ACTIVE

    def test_multiple_workers(self):
        for i in range(3):
            Worker.objects.create(
                id=f"worker-0{i}.pid-{10000 + i}",
                hostname=f"worker-0{i}",
            )
        assert Worker.objects.count() == 3
