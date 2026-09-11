"""
Job serializer — validates and serializes Job model data.

Handles:
- Cron expression validation (via croniter)
- Timezone validation
- Handler existence check
- Schedule-specific field requirements (cron_expression vs run_at)
- next_run_at calculation
"""
from croniter import croniter
from django.utils import timezone as tz
from rest_framework import serializers

from apps.jobs.handlers import get_handler_names
from apps.jobs.models import Job


class RetryPolicySerializer(serializers.Serializer):
    max_attempts = serializers.IntegerField(min_value=1, max_value=10, default=1)
    backoff = serializers.ChoiceField(
        choices=Job.BackoffType.choices, default=Job.BackoffType.FIXED
    )
    base_delay_seconds = serializers.IntegerField(
        min_value=1, max_value=3600, default=30
    )


class JobSerializer(serializers.ModelSerializer):
    retry_policy = RetryPolicySerializer(write_only=True, required=False)
    retry_limit = serializers.IntegerField(read_only=True)
    retry_backoff_type = serializers.CharField(read_only=True)
    retry_base_delay_seconds = serializers.IntegerField(read_only=True)
    next_run_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Job
        fields = [
            "id",
            "name",
            "description",
            "schedule_type",
            "cron_expression",
            "run_at",
            "timezone",
            "priority",
            "timeout_seconds",
            "handler",
            "status",
            "retry_policy",
            "retry_limit",
            "retry_backoff_type",
            "retry_base_delay_seconds",
            "next_run_at",
            "last_run_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "next_run_at",
            "last_run_at",
            "created_at",
            "updated_at",
        ]

    def validate_name(self, value):
        """Name must be snake_case alphanumeric + underscore."""
        import re
        if not re.match(r"^[a-z][a-z0-9_]*$", value):
            raise serializers.ValidationError(
                "Name must be snake_case (lowercase letters, digits, underscores). "
                "Must start with a letter."
            )
        return value

    def validate_handler(self, value):
        """Handler must exist in the registry."""
        available = get_handler_names()
        if value not in available:
            raise serializers.ValidationError(
                f"Handler '{value}' is not registered. "
                f"Available: {available}"
            )
        return value

    def validate_cron_expression(self, value):
        """Validate cron expression syntax."""
        if value is not None:
            try:
                croniter(value)
            except ValueError as e:
                raise serializers.ValidationError(
                    f"Invalid cron expression: {e}"
                )
        return value

    def validate_timezone(self, value):
        """Validate timezone is a valid IANA timezone."""
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(value)
        except (ValueError, zoneinfo.ZoneInfoNotFoundError):
            raise serializers.ValidationError(
                f"Invalid timezone: '{value}'. Use IANA format (e.g., 'UTC', 'America/New_York')."
            )
        return value

    def validate(self, attrs):
        """Cross-field validation for schedule-specific fields."""
        schedule_type = attrs.get("schedule_type")

        if schedule_type == Job.ScheduleType.CRON:
            if not attrs.get("cron_expression"):
                raise serializers.ValidationError(
                    {"cron_expression": "Required for CRON schedule type."}
                )
            if attrs.get("run_at"):
                raise serializers.ValidationError(
                    {"run_at": "Not allowed for CRON schedule type."}
                )
        elif schedule_type == Job.ScheduleType.ONE_TIME:
            if not attrs.get("run_at"):
                raise serializers.ValidationError(
                    {"run_at": "Required for ONE_TIME schedule type."}
                )
            if attrs.get("cron_expression"):
                raise serializers.ValidationError(
                    {"cron_expression": "Not allowed for ONE_TIME schedule type."}
                )
            if attrs.get("run_at") <= tz.now():
                raise serializers.ValidationError(
                    {"run_at": "Must be in the future."}
                )

        return attrs

    def create(self, validated_data):
        """Create job and calculate next_run_at."""
        retry_policy = validated_data.pop("retry_policy", None)
        if retry_policy:
            validated_data["retry_limit"] = retry_policy["max_attempts"]
            validated_data["retry_backoff_type"] = retry_policy["backoff"]
            validated_data["retry_base_delay_seconds"] = retry_policy[
                "base_delay_seconds"
            ]

        job = Job(**validated_data)

        # Calculate next_run_at
        job.next_run_at = self._calculate_next_run(job)
        job.save()
        return job

    def update(self, instance, validated_data):
        """Update job. Recalculate next_run_at if schedule changed."""
        retry_policy = validated_data.pop("retry_policy", None)
        if retry_policy:
            instance.retry_limit = retry_policy["max_attempts"]
            instance.retry_backoff_type = retry_policy["backoff"]
            instance.retry_base_delay_seconds = retry_policy[
                "base_delay_seconds"
            ]

        schedule_changed = False
        for field in ("schedule_type", "cron_expression", "run_at", "timezone"):
            if field in validated_data:
                if getattr(instance, field) != validated_data[field]:
                    schedule_changed = True
                setattr(instance, field, validated_data[field])

        # Update other fields
        for field in ("name", "description", "priority", "timeout_seconds", "handler"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        if schedule_changed:
            instance.next_run_at = self._calculate_next_run(instance)

        instance.save()
        return instance

    def _calculate_next_run(self, job: Job):
        """Calculate next_run_at based on schedule type."""
        if job.schedule_type == Job.ScheduleType.ONE_TIME:
            return job.run_at

        # CRON: use croniter
        cron = croniter(job.cron_expression, tz.now())
        return cron.get_next(tz.datetime)
