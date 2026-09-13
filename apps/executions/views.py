"""
Executions views — read-only API for runs, attempts, workers, and dashboard.

Endpoints:
- GET /api/v1/runs/                    — List all runs (with filters)
- GET /api/v1/runs/{id}/               — Get run details + attempts
- GET /api/v1/attempts/                — List all attempts (with filters)
- GET /api/v1/attempts/{id}/           — Get attempt details
- GET /api/v1/workers/                 — List all workers
- GET /api/v1/workers/{id}/            — Get worker details + running attempts
- GET /api/v1/dashboard/               — Dashboard stats
"""
import logging
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone as tz
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.executions.models import JobAttempt, JobRun, Worker
from apps.jobs.models import Job

logger = logging.getLogger("apps.executions.views")


class JobRunListView(APIView):
    """List all runs with optional filters."""

    def get(self, request):
        """List all runs with optional status/job_id filters."""
        queryset = JobRun.objects.select_related("job").order_by("-created_at")

        # Filters
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        job_id = request.query_params.get("job_id")
        if job_id:
            queryset = queryset.filter(job_id=job_id)

        # Pagination
        limit = min(int(request.query_params.get("limit", 50)), 200)
        offset = int(request.query_params.get("offset", 0))
        queryset = queryset[offset : offset + limit]

        data = [
            {
                "id": str(run.id),
                "job_id": str(run.job_id),
                "job_name": run.job.name,
                "status": run.status,
                "scheduled_for": run.scheduled_for.isoformat(),
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "total_attempts": run.total_attempts,
                "created_at": run.created_at.isoformat(),
            }
            for run in queryset
        ]
        return Response(data)


class JobRunDetailView(APIView):
    """Get run details with all its attempts."""

    def get(self, request, run_id):
        try:
            run = JobRun.objects.select_related("job").get(id=run_id)
        except JobRun.DoesNotExist:
            return Response(
                {"error": "Run not found"}, status=404
            )

        attempts = JobAttempt.objects.filter(job_run=run).order_by("attempt_number")

        return Response({
            "id": str(run.id),
            "job_id": str(run.job_id),
            "job_name": run.job.name,
            "status": run.status,
            "scheduled_for": run.scheduled_for.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "total_attempts": run.total_attempts,
            "created_at": run.created_at.isoformat(),
            "attempts": [
                {
                    "id": str(a.id),
                    "attempt_number": a.attempt_number,
                    "status": a.status,
                    "priority": a.priority,
                    "worker_id": a.worker_id,
                    "fencing_token": a.fencing_token,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                    "duration_ms": int(a.duration.total_seconds() * 1000) if a.duration else None,
                    "error_message": a.error_message,
                }
                for a in attempts
            ],
        })


class JobAttemptListView(APIView):
    """List all attempts with optional filters."""

    def get(self, request):
        queryset = JobAttempt.objects.select_related("job", "job_run").order_by(
            "-created_at"
        )

        # Filters
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        worker_id = request.query_params.get("worker_id")
        if worker_id:
            queryset = queryset.filter(worker_id=worker_id)

        job_id = request.query_params.get("job_id")
        if job_id:
            queryset = queryset.filter(job_id=job_id)

        priority = request.query_params.get("priority")
        if priority:
            queryset = queryset.filter(priority=priority)

        # Pagination
        limit = min(int(request.query_params.get("limit", 50)), 200)
        offset = int(request.query_params.get("offset", 0))
        queryset = queryset[offset : offset + limit]

        data = [
            {
                "id": str(a.id),
                "job_run_id": str(a.job_run_id),
                "job_id": str(a.job_id),
                "job_name": a.job.name,
                "attempt_number": a.attempt_number,
                "status": a.status,
                "priority": a.priority,
                "worker_id": a.worker_id,
                "fencing_token": a.fencing_token,
                "scheduled_for": a.scheduled_for.isoformat(),
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                "duration_ms": int(a.duration.total_seconds() * 1000) if a.duration else None,
                "error_message": a.error_message,
                "created_at": a.created_at.isoformat(),
            }
            for a in queryset
        ]
        return Response(data)


class JobAttemptDetailView(APIView):
    """Get attempt details."""

    def get(self, request, attempt_id):
        try:
            a = JobAttempt.objects.select_related("job", "job_run").get(
                id=attempt_id
            )
        except JobAttempt.DoesNotExist:
            return Response(
                {"error": "Attempt not found"}, status=404
            )

        return Response({
            "id": str(a.id),
            "job_run_id": str(a.job_run_id),
            "job_id": str(a.job_id),
            "job_name": a.job.name,
            "attempt_number": a.attempt_number,
            "status": a.status,
            "priority": a.priority,
            "worker_id": a.worker_id,
            "fencing_token": a.fencing_token,
            "lease_expires_at": a.lease_expires_at.isoformat() if a.lease_expires_at else None,
            "scheduled_for": a.scheduled_for.isoformat(),
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "finished_at": a.finished_at.isoformat() if a.finished_at else None,
            "duration_ms": int(a.duration.total_seconds() * 1000) if a.duration else None,
            "error_message": a.error_message,
            "created_at": a.created_at.isoformat(),
        })


class WorkerListView(APIView):
    """List all workers."""

    def get(self, request):
        queryset = Worker.objects.order_by("-last_heartbeat_at")

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        data = [
            {
                "id": w.id,
                "hostname": w.hostname,
                "status": w.status,
                "started_at": w.started_at.isoformat(),
                "last_heartbeat_at": w.last_heartbeat_at.isoformat(),
                "is_alive": (tz.now() - w.last_heartbeat_at) < timedelta(seconds=60),
            }
            for w in queryset
        ]
        return Response(data)


class WorkerDetailView(APIView):
    """Get worker details with its running attempts."""

    def get(self, request, worker_id):
        try:
            w = Worker.objects.get(id=worker_id)
        except Worker.DoesNotExist:
            return Response(
                {"error": "Worker not found"}, status=404
            )

        running_attempts = JobAttempt.objects.filter(
            worker_id=worker_id,
            status=JobAttempt.Status.RUNNING,
        )

        return Response({
            "id": w.id,
            "hostname": w.hostname,
            "status": w.status,
            "started_at": w.started_at.isoformat(),
            "last_heartbeat_at": w.last_heartbeat_at.isoformat(),
            "is_alive": (tz.now() - w.last_heartbeat_at) < timedelta(seconds=60),
            "running_attempts": [
                {
                    "id": str(a.id),
                    "job_id": str(a.job_id),
                    "attempt_number": a.attempt_number,
                    "priority": a.priority,
                    "fencing_token": a.fencing_token,
                    "lease_expires_at": a.lease_expires_at.isoformat() if a.lease_expires_at else None,
                }
                for a in running_attempts
            ],
        })


class DashboardView(APIView):
    """Dashboard stats — success rate, avg duration, queue depth, etc."""

    def get(self, request):
        now = tz.now()
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

        # Run stats (last 24h)
        runs_24h = JobRun.objects.filter(created_at__gte=last_24h)
        run_stats_24h = runs_24h.aggregate(
            total=Count("id"),
            success=Count("id", filter=Q(status=JobRun.Status.SUCCESS)),
            failed=Count("id", filter=Q(status=JobRun.Status.PERMANENTLY_FAILED)),
        )

        # Run stats (last 7d)
        runs_7d = JobRun.objects.filter(created_at__gte=last_7d)
        run_stats_7d = runs_7d.aggregate(
            total=Count("id"),
            success=Count("id", filter=Q(status=JobRun.Status.SUCCESS)),
            failed=Count("id", filter=Q(status=JobRun.Status.PERMANENTLY_FAILED)),
        )

        # Attempt stats (last 24h)
        attempts_24h = JobAttempt.objects.filter(created_at__gte=last_24h)
        attempt_stats_24h = attempts_24h.aggregate(
            total=Count("id"),
            avg_duration=Avg("duration"),
        )

        # Queue depth
        queued_runs = JobRun.objects.filter(status=JobRun.Status.QUEUED).count()
        queued_attempts = JobAttempt.objects.filter(
            status=JobAttempt.Status.QUEUED
        ).count()
        running_attempts = JobAttempt.objects.filter(
            status=JobAttempt.Status.RUNNING
        ).count()

        # Worker stats
        active_workers = Worker.objects.filter(
            status=Worker.Status.ACTIVE,
            last_heartbeat_at__gte=now - timedelta(seconds=60),
        ).count()
        dead_workers = Worker.objects.filter(
            status=Worker.Status.DEAD,
        ).count()

        # Success rates
        def success_rate(stats):
            if stats["total"] == 0:
                return 0.0
            return round(stats["success"] / stats["total"] * 100, 1)

        # Avg duration in ms
        avg_duration_ms = None
        if attempt_stats_24h["avg_duration"]:
            avg_duration_ms = int(
                attempt_stats_24h["avg_duration"].total_seconds() * 1000
            )

        # Total jobs
        total_jobs = Job.objects.filter(
            status__in=[Job.Status.ACTIVE, Job.Status.INACTIVE]
        ).count()
        active_jobs = Job.objects.filter(status=Job.Status.ACTIVE).count()

        return Response({
            "runs_24h": {
                "total": run_stats_24h["total"],
                "success": run_stats_24h["success"],
                "failed": run_stats_24h["failed"],
                "success_rate": success_rate(run_stats_24h),
            },
            "runs_7d": {
                "total": run_stats_7d["total"],
                "success": run_stats_7d["success"],
                "failed": run_stats_7d["failed"],
                "success_rate": success_rate(run_stats_7d),
            },
            "attempts_24h": {
                "total": attempt_stats_24h["total"],
                "avg_duration_ms": avg_duration_ms,
            },
            "queue": {
                "queued_runs": queued_runs,
                "queued_attempts": queued_attempts,
                "running_attempts": running_attempts,
            },
            "workers": {
                "active": active_workers,
                "dead": dead_workers,
            },
            "jobs": {
                "total": total_jobs,
                "active": active_jobs,
            },
        })


class CleanupOldRunsView(APIView):
    """Trigger cleanup of old execution records (dry-run by default)."""

    def post(self, request):
        days = int(request.data.get("days", 90))
        execute = request.data.get("execute", False)
        cutoff = tz.now() - timedelta(days=days)

        run_count = JobRun.objects.filter(created_at__lt=cutoff).count()
        attempt_count = JobAttempt.objects.filter(created_at__lt=cutoff).count()

        if execute:
            JobAttempt.objects.filter(created_at__lt=cutoff).delete()
            JobRun.objects.filter(created_at__lt=cutoff).delete()
            return Response({
                "status": "executed",
                "message": f"Deleted {run_count} runs and {attempt_count} attempts older than {days} days",
                "runs_deleted": run_count,
                "attempts_deleted": attempt_count,
            })

        return Response({
            "status": "dry_run",
            "message": f"Would delete {run_count} runs and {attempt_count} attempts older than {days} days (send execute=true to apply)",
            "runs_to_delete": run_count,
            "attempts_to_delete": attempt_count,
        })


class ResetStuckRunsView(APIView):
    """Reset runs stuck in QUEUED/RUNNING for too long."""

    def post(self, request):
        timeout = int(request.data.get("timeout_minutes", 60))
        execute = request.data.get("execute", False)
        cutoff = tz.now() - timedelta(minutes=timeout)

        stuck_runs = JobRun.objects.filter(
            status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING],
            created_at__lt=cutoff,
        )
        stuck_attempts = JobAttempt.objects.filter(
            status__in=[JobAttempt.Status.QUEUED, JobAttempt.Status.RUNNING],
            created_at__lt=cutoff,
        )
        run_count = stuck_runs.count()
        attempt_count = stuck_attempts.count()

        if execute:
            stuck_attempts.update(
                status=JobAttempt.Status.QUEUED,
                worker_id=None,
                fencing_token=0,
                lease_expires_at=None,
                started_at=None,
                error_message="Reset by ops endpoint",
            )
            stuck_runs.update(
                status=JobRun.Status.QUEUED,
                started_at=None,
            )
            return Response({
                "status": "executed",
                "message": f"Reset {run_count} runs and {attempt_count} attempts stuck for >{timeout}min",
                "runs_reset": run_count,
                "attempts_reset": attempt_count,
            })

        return Response({
            "status": "dry_run",
            "message": f"Would reset {run_count} runs and {attempt_count} attempts stuck for >{timeout}min (send execute=true to apply)",
            "runs_to_reset": run_count,
            "attempts_to_reset": attempt_count,
        })
