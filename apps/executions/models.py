"""
Execution models — JobRun, JobAttempt, Worker, OutboxEvent.

These models track the operational state of the system:
- JobRun: one scheduled occurrence of a job
- JobAttempt: one execution attempt of a job run
- Worker: registered worker process
- OutboxEvent: reliable event publishing via the Outbox Pattern
"""
import uuid

from django.db import models
from django.utils import timezone


class JobRun(models.Model):
    """One scheduled occurrence of a job."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        PERMANENTLY_FAILED = "PERMANENTLY_FAILED", "Permanently Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="runs",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    scheduled_for = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total_attempts = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "job_run"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run {self.id} for job {self.job_id} ({self.status})"


class JobAttempt(models.Model):
    """One execution attempt of a job run."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        TIMED_OUT = "TIMED_OUT", "Timed Out"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_run = models.ForeignKey(
        JobRun,
        on_delete=models.CASCADE,
        related_name="attempts",
        db_index=True,
    )
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="attempts",
        db_index=True,
    )
    attempt_number = models.IntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    priority = models.CharField(max_length=20, db_index=True)
    worker_id = models.CharField(
        max_length=100, null=True, blank=True, db_index=True
    )
    fencing_token = models.IntegerField(default=0)
    lease_expires_at = models.DateTimeField(
        null=True, blank=True, db_index=True
    )
    scheduled_for = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration = models.DurationField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "job_attempt"
        ordering = ["-created_at"]

        indexes = [
            # Worker query: find claimable attempts quickly
            models.Index(
                fields=["scheduled_for", "priority"],
                name="idx_attempt_claimable",
                condition=models.Q(status="QUEUED"),
            ),
            # History queries
            models.Index(
                fields=["job_id", "created_at"],
                name="idx_attempt_job_id",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="idx_attempt_status",
            ),
        ]

    def __str__(self):
        return (
            f"Attempt {self.attempt_number} for run {self.job_run_id} "
            f"({self.status})"
        )


class Worker(models.Model):
    """Registered worker process."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DEAD = "DEAD", "Dead"

    id = models.CharField(max_length=100, primary_key=True)
    hostname = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    started_at = models.DateTimeField(default=timezone.now)
    last_heartbeat_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "worker"

    def __str__(self):
        return f"Worker {self.id} ({self.status})"


class OutboxEvent(models.Model):
    """Reliable event publishing via the Outbox Pattern."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PUBLISHED = "PUBLISHED", "Published"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=100, db_index=True)
    aggregate_id = models.UUIDField(db_index=True)
    payload = models.JSONField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "outbox_event"
        ordering = ["created_at"]

    def __str__(self):
        return f"Event {self.event_type} for {self.aggregate_id} ({self.status})"
