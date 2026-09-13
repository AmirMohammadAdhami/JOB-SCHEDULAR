"""Tests for executions API endpoints."""
import pytest
from datetime import timedelta

from django.utils import timezone as tz
from rest_framework.test import APIClient

from apps.executions.models import JobAttempt, JobRun, Worker
from apps.jobs.models import Job


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def job(db):
    return Job.objects.create(
        name="api_test_job",
        description="Test",
        schedule_type=Job.ScheduleType.CRON,
        cron_expression="* * * * *",
        handler="billing_reconciliation",
        priority=Job.Priority.HIGH,
        next_run_at=tz.now(),
    )


@pytest.fixture
def run(job):
    return JobRun.objects.create(
        job=job,
        scheduled_for=tz.now(),
        status=JobRun.Status.SUCCESS,
        started_at=tz.now(),
        finished_at=tz.now(),
    )


@pytest.fixture
def attempt(job, run):
    return JobAttempt.objects.create(
        job_run=run,
        job=job,
        attempt_number=1,
        status=JobAttempt.Status.SUCCESS,
        priority="HIGH",
        worker_id="worker-1",
        fencing_token=1,
        scheduled_for=tz.now(),
        started_at=tz.now(),
        finished_at=tz.now(),
        duration=timedelta(seconds=5),
    )


@pytest.fixture
def worker(db):
    return Worker.objects.create(
        id="test-worker-api",
        hostname="test-host",
        status=Worker.Status.ACTIVE,
        last_heartbeat_at=tz.now(),
    )


@pytest.mark.django_db
class TestRunList:
    def test_list_runs(self, api_client, run):
        resp = api_client.get("/api/v1/runs/")
        assert resp.status_code == 200
        assert len(resp.data) == 1
        assert resp.data[0]["job_name"] == "api_test_job"
        assert resp.data[0]["status"] == "SUCCESS"

    def test_filter_by_status(self, api_client, run):
        resp = api_client.get("/api/v1/runs/?status=SUCCESS")
        assert resp.status_code == 200
        assert len(resp.data) == 1

        resp = api_client.get("/api/v1/runs/?status=FAILED")
        assert resp.status_code == 200
        assert len(resp.data) == 0

    def test_filter_by_job_id(self, api_client, run, job):
        resp = api_client.get(f"/api/v1/runs/?job_id={job.id}")
        assert resp.status_code == 200
        assert len(resp.data) == 1

    def test_pagination(self, api_client, job):
        for i in range(5):
            JobRun.objects.create(
                job=job,
                scheduled_for=tz.now(),
                status=JobRun.Status.QUEUED,
            )
        resp = api_client.get("/api/v1/runs/?limit=2")
        assert resp.status_code == 200
        assert len(resp.data) == 2


@pytest.mark.django_db
class TestRunDetail:
    def test_get_run(self, api_client, run, attempt):
        resp = api_client.get(f"/api/v1/runs/{run.id}/")
        assert resp.status_code == 200
        assert resp.data["id"] == str(run.id)
        assert len(resp.data["attempts"]) == 1

    def test_run_not_found(self, api_client):
        resp = api_client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestAttemptList:
    def test_list_attempts(self, api_client, attempt):
        resp = api_client.get("/api/v1/attempts/")
        assert resp.status_code == 200
        assert len(resp.data) == 1
        assert resp.data[0]["status"] == "SUCCESS"

    def test_filter_by_status(self, api_client, attempt):
        resp = api_client.get("/api/v1/attempts/?status=SUCCESS")
        assert len(resp.data) == 1

        resp = api_client.get("/api/v1/attempts/?status=RUNNING")
        assert len(resp.data) == 0

    def test_filter_by_worker(self, api_client, attempt):
        resp = api_client.get("/api/v1/attempts/?worker_id=worker-1")
        assert len(resp.data) == 1

        resp = api_client.get("/api/v1/attempts/?worker_id=other-worker")
        assert len(resp.data) == 0

    def test_filter_by_priority(self, api_client, attempt):
        resp = api_client.get("/api/v1/attempts/?priority=HIGH")
        assert len(resp.data) == 1

        resp = api_client.get("/api/v1/attempts/?priority=LOW")
        assert len(resp.data) == 0


@pytest.mark.django_db
class TestAttemptDetail:
    def test_get_attempt(self, api_client, attempt):
        resp = api_client.get(f"/api/v1/attempts/{attempt.id}/")
        assert resp.status_code == 200
        assert resp.data["id"] == str(attempt.id)
        assert resp.data["duration_ms"] == 5000

    def test_attempt_not_found(self, api_client):
        resp = api_client.get(
            "/api/v1/attempts/00000000-0000-0000-0000-000000000000/"
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestWorkerList:
    def test_list_workers(self, api_client, worker):
        resp = api_client.get("/api/v1/workers/")
        assert resp.status_code == 200
        assert len(resp.data) == 1
        assert resp.data[0]["id"] == "test-worker-api"
        assert resp.data[0]["is_alive"] is True

    def test_filter_by_status(self, api_client, worker):
        resp = api_client.get("/api/v1/workers/?status=ACTIVE")
        assert len(resp.data) == 1

        resp = api_client.get("/api/v1/workers/?status=DEAD")
        assert len(resp.data) == 0

    def test_dead_worker_not_alive(self, api_client, db):
        w = Worker.objects.create(
            id="dead-worker",
            hostname="dead",
            status=Worker.Status.DEAD,
            last_heartbeat_at=tz.now() - timedelta(minutes=5),
        )
        resp = api_client.get("/api/v1/workers/")
        assert resp.data[0]["is_alive"] is False


@pytest.mark.django_db
class TestWorkerDetail:
    def test_get_worker(self, api_client, worker, attempt):
        resp = api_client.get(f"/api/v1/workers/{worker.id}/")
        assert resp.status_code == 200
        assert resp.data["id"] == worker.id

    def test_worker_not_found(self, api_client):
        resp = api_client.get("/api/v1/workers/nonexistent/")
        assert resp.status_code == 404

    def test_worker_running_attempts(self, api_client, worker, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.RUNNING,
        )
        JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.RUNNING,
            priority="HIGH",
            worker_id="test-worker-api",
            scheduled_for=tz.now(),
        )
        resp = api_client.get(f"/api/v1/workers/{worker.id}/")
        assert len(resp.data["running_attempts"]) == 1


@pytest.mark.django_db
class TestDashboard:
    def test_dashboard_empty(self, api_client):
        resp = api_client.get("/api/v1/dashboard/")
        assert resp.status_code == 200
        assert resp.data["runs_24h"]["total"] == 0
        assert resp.data["jobs"]["total"] == 0
        assert resp.data["workers"]["active"] == 0

    def test_dashboard_with_data(self, api_client, run, attempt, worker, job):
        resp = api_client.get("/api/v1/dashboard/")
        assert resp.data["runs_24h"]["total"] == 1
        assert resp.data["runs_24h"]["success"] == 1
        assert resp.data["runs_24h"]["success_rate"] == 100.0
        assert resp.data["workers"]["active"] == 1
        assert resp.data["jobs"]["total"] == 1
        assert resp.data["jobs"]["active"] == 1

    def test_dashboard_queue_depth(self, api_client, job):
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.QUEUED,
        )
        JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority="HIGH",
            scheduled_for=tz.now(),
        )
        resp = api_client.get("/api/v1/dashboard/")
        assert resp.data["queue"]["queued_runs"] == 1
        assert resp.data["queue"]["queued_attempts"] == 1
