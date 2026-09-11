"""Tests for the Job API endpoints."""
import pytest
from django.utils import timezone as tz
from rest_framework import status
from rest_framework.test import APIClient

from apps.jobs.models import Job


@pytest.fixture
def api_client():
    return APIClient()


CRON_PAYLOAD = {
    "name": "test_cron_job",
    "description": "A test cron job",
    "schedule_type": "CRON",
    "cron_expression": "0 3 * * *",
    "timezone": "UTC",
    "priority": "MEDIUM",
    "timeout_seconds": 300,
    "handler": "billing_reconciliation",
    "retry_policy": {
        "max_attempts": 3,
        "backoff": "EXPONENTIAL",
        "base_delay_seconds": 30,
    },
}

ONE_TIME_PAYLOAD = {
    "name": "test_one_time_job",
    "description": "A one-time job",
    "schedule_type": "ONE_TIME",
    "run_at": (tz.now() + tz.timedelta(hours=1)).isoformat(),
    "priority": "HIGH",
    "timeout_seconds": 600,
    "handler": "generate_daily_report",
    "retry_policy": {
        "max_attempts": 1,
        "backoff": "FIXED",
        "base_delay_seconds": 10,
    },
}


@pytest.mark.django_db
class TestCreateJob:
    def test_create_cron_job(self, api_client):
        response = api_client.post(
            "/api/v1/jobs/", CRON_PAYLOAD, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["name"] == "test_cron_job"
        assert data["schedule_type"] == "CRON"
        assert data["status"] == "ACTIVE"
        assert data["next_run_at"] is not None
        assert data["handler"] == "billing_reconciliation"
        assert data["retry_limit"] == 3
        assert data["retry_backoff_type"] == "EXPONENTIAL"

    def test_create_one_time_job(self, api_client):
        response = api_client.post(
            "/api/v1/jobs/", ONE_TIME_PAYLOAD, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["schedule_type"] == "ONE_TIME"
        assert data["next_run_at"] is not None

    def test_invalid_handler(self, api_client):
        payload = {**CRON_PAYLOAD, "name": "bad_handler", "handler": "nonexistent"}
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "handler" in response.json()

    def test_invalid_cron_expression(self, api_client):
        payload = {
            **CRON_PAYLOAD,
            "name": "bad_cron",
            "cron_expression": "invalid cron",
        }
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cron_expression" in response.json()

    def test_cron_requires_cron_expression(self, api_client):
        payload = {
            **CRON_PAYLOAD,
            "name": "no_cron_expr",
            "cron_expression": None,
        }
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_one_time_requires_run_at(self, api_client):
        payload = {
            "name": "no_run_at",
            "description": "Missing run_at",
            "schedule_type": "ONE_TIME",
            "priority": "MEDIUM",
            "timeout_seconds": 300,
            "handler": "billing_reconciliation",
        }
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_one_time_run_at_must_be_future(self, api_client):
        payload = {
            **ONE_TIME_PAYLOAD,
            "name": "past_run_at",
            "run_at": (tz.now() - tz.timedelta(hours=1)).isoformat(),
        }
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_name_format(self, api_client):
        payload = {**CRON_PAYLOAD, "name": "Invalid-Name!!!"}
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_duplicate_name(self, api_client):
        api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        payload = {**CRON_PAYLOAD, "name": "test_cron_job"}
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_timeout(self, api_client):
        payload = {**CRON_PAYLOAD, "name": "bad_timeout", "timeout_seconds": 3}
        response = api_client.post("/api/v1/jobs/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestListJobs:
    def test_list_empty(self, api_client):
        response = api_client.get("/api/v1/jobs/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    def test_list_jobs(self, api_client):
        api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        api_client.post(
            "/api/v1/jobs/",
            {**CRON_PAYLOAD, "name": "second_job"},
            format="json",
        )
        response = api_client.get("/api/v1/jobs/")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 2

    def test_filter_by_status(self, api_client):
        api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        response = api_client.get("/api/v1/jobs/?status=ACTIVE")
        assert len(response.json()) == 1

        response = api_client.get("/api/v1/jobs/?status=INACTIVE")
        assert len(response.json()) == 0

    def test_filter_by_priority(self, api_client):
        api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        api_client.post(
            "/api/v1/jobs/",
            {**CRON_PAYLOAD, "name": "critical_job", "priority": "CRITICAL"},
            format="json",
        )
        response = api_client.get("/api/v1/jobs/?priority=CRITICAL")
        assert len(response.json()) == 1
        assert response.json()[0]["priority"] == "CRITICAL"


@pytest.mark.django_db
class TestGetJob:
    def test_get_job(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        response = api_client.get(f"/api/v1/jobs/{job_id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == job_id

    def test_get_nonexistent(self, api_client):
        response = api_client.get(
            "/api/v1/jobs/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestUpdateJob:
    def test_update_priority(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        response = api_client.patch(
            f"/api/v1/jobs/{job_id}/",
            {"priority": "CRITICAL"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["priority"] == "CRITICAL"

    def test_update_schedule(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        old_next = resp.json()["next_run_at"]
        response = api_client.patch(
            f"/api/v1/jobs/{job_id}/",
            {"cron_expression": "*/5 * * * *"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        # next_run_at should be recalculated
        new_next = response.json()["next_run_at"]
        assert new_next is not None


@pytest.mark.django_db
class TestDeleteJob:
    def test_soft_delete(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        response = api_client.delete(f"/api/v1/jobs/{job_id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Job should still exist but be INACTIVE
        job = Job.objects.get(id=job_id)
        assert job.status == Job.Status.INACTIVE

    def test_delete_nonexistent(self, api_client):
        response = api_client.delete(
            "/api/v1/jobs/00000000-0000-0000-0000-000000000000/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestTriggerJob:
    def test_trigger_active_job(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        response = api_client.post(f"/api/v1/jobs/{job_id}/trigger/")
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["run_id"] is not None
        assert data["attempt_id"] is not None
        assert data["status"] == "QUEUED"

    def test_trigger_inactive_job(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        # Soft-delete first
        api_client.delete(f"/api/v1/jobs/{job_id}/")
        response = api_client.post(f"/api/v1/jobs/{job_id}/trigger/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_trigger_nonexistent(self, api_client):
        response = api_client.post(
            "/api/v1/jobs/00000000-0000-0000-0000-000000000000/trigger/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestJobRuns:
    def test_get_runs(self, api_client):
        resp = api_client.post("/api/v1/jobs/", CRON_PAYLOAD, format="json")
        job_id = resp.json()["id"]
        # Trigger twice
        api_client.post(f"/api/v1/jobs/{job_id}/trigger/")
        api_client.post(f"/api/v1/jobs/{job_id}/trigger/")
        response = api_client.get(f"/api/v1/jobs/{job_id}/runs/")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 2

    def test_get_runs_nonexistent_job(self, api_client):
        response = api_client.get(
            "/api/v1/jobs/00000000-0000-0000-0000-000000000000/runs/"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
