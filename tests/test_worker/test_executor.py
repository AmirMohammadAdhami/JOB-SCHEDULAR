"""Tests for worker executor."""
import pytest
from unittest.mock import patch

from worker.executor import execute_handler, submit_job


@pytest.mark.django_db
class TestExecuteHandler:
    def test_execute_successful_handler(self):
        result = execute_handler(
            handler_name="billing_reconciliation",
            attempt_id="test-123",
        )
        assert result["success"] is True
        assert result["duration_ms"] >= 0
        assert result["error"] is None

    def test_execute_unknown_handler(self):
        result = execute_handler(
            handler_name="nonexistent_handler",
            attempt_id="test-123",
        )
        assert result["success"] is False
        assert result["error"] is not None

    def test_execute_handler_with_exception(self):
        with patch("worker.executor.get_handler") as mock:
            mock.side_effect = ValueError("Test error")
            result = execute_handler(
                handler_name="any_handler",
                attempt_id="test-123",
            )
            assert result["success"] is False
            assert "Test error" in result["error"]

    def test_execute_all_handlers(self):
        """All registered handlers should execute successfully."""
        from apps.jobs.handlers import get_handler_names
        for name in get_handler_names():
            result = execute_handler(handler_name=name, attempt_id="test")
            assert result["success"] is True, f"Handler {name} failed"


@pytest.mark.django_db
class TestSubmitJob:
    def test_submit_returns_future(self):
        future = submit_job(
            handler_name="billing_reconciliation",
            attempt_id="test-123",
        )
        assert future is not None
        # Wait for completion
        result = future.result(timeout=10)
        assert result["success"] is True

    def test_submit_multiple_jobs(self):
        futures = []
        for i in range(3):
            future = submit_job(
                handler_name="billing_reconciliation",
                attempt_id=f"test-{i}",
            )
            futures.append(future)

        for f in futures:
            result = f.result(timeout=10)
            assert result["success"] is True
