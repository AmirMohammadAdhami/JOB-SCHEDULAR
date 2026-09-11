"""
Job model — the definition and configuration of a scheduled job.

This is the immutable "what to run" record. The scheduler reads this
to determine when to create JobRun + JobAttempt records.
"""
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone as tz


class Job(models.Model):
    """A scheduled job definition."""

    class ScheduleType(models.TextChoices):
        CRON = "CRON", "Cron"
        ONE_TIME = "ONE_TIME", "One Time"

    class Priority(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"

    class BackoffType(models.TextChoices):
        FIXED = "FIXED", "Fixed"
        EXPONENTIAL = "EXPONENTIAL", "Exponential"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        DELETED = "DELETED", "Deleted"

    # Primary key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Core fields
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField()

    # Schedule
    schedule_type = models.CharField(
        max_length=20, choices=ScheduleType.choices
    )
    cron_expression = models.CharField(
        max_length=100, null=True, blank=True
    )
    run_at = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=50, default="UTC")

    # Execution config
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    timeout_seconds = models.IntegerField(
        default=300,
        validators=[MinValueValidator(10), MaxValueValidator(7200)],
    )
    handler = models.CharField(max_length=100, db_index=True)

    # Retry config
    retry_limit = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    retry_backoff_type = models.CharField(
        max_length=20,
        choices=BackoffType.choices,
        default=BackoffType.FIXED,
    )
    retry_base_delay_seconds = models.IntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(3600)],
    )

    # State
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(default=tz.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "job"
        ordering = ["name"]

        indexes = [
            # Partial index: only active jobs, used by scheduler tick query
            models.Index(
                fields=["next_run_at"],
                name="idx_job_next_run_at",
                condition=models.Q(status="ACTIVE"),
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.schedule_type})"

    def clean(self):
        """Validate schedule fields."""
        from django.core.exceptions import ValidationError

        if self.schedule_type == self.ScheduleType.CRON:
            if not self.cron_expression:
                raise ValidationError(
                    {"cron_expression": "Required for CRON schedule type."}
                )
        elif self.schedule_type == self.ScheduleType.ONE_TIME:
            if not self.run_at:
                raise ValidationError(
                    {"run_at": "Required for ONE_TIME schedule type."}
                )
