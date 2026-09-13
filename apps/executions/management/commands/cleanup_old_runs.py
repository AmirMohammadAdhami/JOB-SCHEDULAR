"""
Management command: cleanup_old_runs

Deletes execution records older than N days to keep the database lean.
Default: 90 days. Dry-run by default (use --execute to apply).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone as tz

from apps.executions.models import JobAttempt, JobRun


class Command(BaseCommand):
    help = "Delete old execution records (runs + attempts)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Delete records older than N days (default: 90)",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually delete records (dry-run by default)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        execute = options["execute"]
        cutoff = tz.now() - tz.timedelta(days=days)

        old_runs = JobRun.objects.filter(created_at__lt=cutoff)
        run_count = old_runs.count()

        old_attempts = JobAttempt.objects.filter(created_at__lt=cutoff)
        attempt_count = old_attempts.count()

        if not execute:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would delete {run_count} runs and "
                    f"{attempt_count} attempts older than {days} days "
                    f"(before {cutoff.isoformat()})"
                )
            )
            return

        old_attempts.delete()
        old_runs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {run_count} runs and {attempt_count} attempts "
                f"older than {days} days"
            )
        )
