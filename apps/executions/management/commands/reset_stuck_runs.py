"""
Management command: reset_stuck_runs

Finds runs stuck in QUEUED/RUNNING state for too long and resets them.
Useful after a scheduler or worker crash.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun


class Command(BaseCommand):
    help = "Reset runs stuck in QUEUED/RUNNING for longer than the timeout"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout-minutes",
            type=int,
            default=60,
            help="Consider runs stuck after N minutes (default: 60)",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually reset records (dry-run by default)",
        )

    def handle(self, *args, **options):
        timeout = options["timeout_minutes"]
        execute = options["execute"]
        cutoff = tz.now() - timedelta(minutes=timeout)

        stuck_runs = JobRun.objects.filter(
            status__in=[JobRun.Status.QUEUED, JobRun.Status.RUNNING],
            created_at__lt=cutoff,
        )
        run_count = stuck_runs.count()

        stuck_attempts = JobAttempt.objects.filter(
            status__in=[JobAttempt.Status.QUEUED, JobAttempt.Status.RUNNING],
            created_at__lt=cutoff,
        )
        attempt_count = stuck_attempts.count()

        if not execute:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would reset {run_count} runs and "
                    f"{attempt_count} attempts stuck for >{timeout}min"
                )
            )
            return

        stuck_attempts.update(
            status=JobAttempt.Status.QUEUED,
            worker_id=None,
            fencing_token=0,
            lease_expires_at=None,
            started_at=None,
            error_message="Reset by cleanup command",
        )

        stuck_runs.update(
            status=JobRun.Status.QUEUED,
            started_at=None,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reset {run_count} runs and {attempt_count} attempts "
                f"stuck for >{timeout}min"
            )
        )
