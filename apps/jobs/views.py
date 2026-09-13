"""
Job views — REST API endpoints for job management.

Endpoints:
- GET    /api/v1/jobs/           — List all jobs
- POST   /api/v1/jobs/           — Create a job
- GET    /api/v1/jobs/{id}/      — Get job details
- PATCH  /api/v1/jobs/{id}/      — Update job
- DELETE /api/v1/jobs/{id}/      — Soft-delete job (set status=INACTIVE)
- POST   /api/v1/jobs/{id}/trigger/ — Manually trigger a job
- GET    /api/v1/jobs/{id}/runs/ — Get execution history
"""
import logging

from django.utils import timezone as tz
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.executions.models import JobRun
from apps.jobs.models import Job
from apps.jobs.serializers import JobSerializer

logger = logging.getLogger("apps.jobs.views")


class JobListView(APIView):
    """List all jobs, or create a new job."""

    def get(self, request):
        """List all jobs with optional status/priority filters."""
        queryset = Job.objects.filter(status__in=[Job.Status.ACTIVE, Job.Status.INACTIVE])

        # Optional filters
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        priority_filter = request.query_params.get("priority")
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter)

        serializer = JobSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = JobSerializer(data=request.data)
        if serializer.is_valid():
            job = serializer.save()
            return Response(
                JobSerializer(job).data, status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class JobDetailView(APIView):
    """Get, update, or delete a specific job."""

    def _get_job(self, job_id):
        try:
            return Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return None

    def get(self, request, job_id):
        job = self._get_job(job_id)
        if not job:
            return Response(
                {"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(JobSerializer(job).data)

    def patch(self, request, job_id):
        job = self._get_job(job_id)
        if not job:
            return Response(
                {"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND
            )
        serializer = JobSerializer(job, data=request.data, partial=True)
        if serializer.is_valid():
            job = serializer.save()
            return Response(JobSerializer(job).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, job_id):
        job = self._get_job(job_id)
        if not job:
            return Response(
                {"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND
            )
        # Soft-delete: set status to INACTIVE
        job.status = Job.Status.INACTIVE
        job.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class JobTriggerView(APIView):
    """Manually trigger a job (for testing)."""

    def post(self, request, job_id):
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if job.status != Job.Status.ACTIVE:
            return Response(
                {"error": "Job must be ACTIVE to trigger"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create a JobRun + JobAttempt immediately
        run = JobRun.objects.create(
            job=job,
            scheduled_for=tz.now(),
            status=JobRun.Status.QUEUED,
        )
        from apps.executions.models import JobAttempt

        attempt = JobAttempt.objects.create(
            job_run=run,
            job=job,
            attempt_number=1,
            status=JobAttempt.Status.QUEUED,
            priority=job.priority,
            scheduled_for=tz.now(),
        )

        logger.info(
            f"Manually triggered job {job.name}: run={run.id}, attempt={attempt.id}"
        )

        return Response(
            {
                "run_id": str(run.id),
                "attempt_id": str(attempt.id),
                "status": "QUEUED",
            },
            status=status.HTTP_201_CREATED,
        )


class JobRunListView(APIView):
    """Get execution history for a job."""

    def get(self, request, job_id):
        try:
            Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND
            )

        runs = JobRun.objects.filter(job_id=job_id).order_by("-created_at")

        data = [
            {
                "id": str(run.id),
                "status": run.status,
                "scheduled_for": run.scheduled_for.isoformat(),
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "total_attempts": run.total_attempts,
                "created_at": run.created_at.isoformat(),
            }
            for run in runs
        ]
        return Response(data)
