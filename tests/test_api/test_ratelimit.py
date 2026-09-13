"""
Tests for rate limiting middleware.
"""
import time
from unittest.mock import MagicMock, patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from nexusops.ratelimit import RATE_LIMITS, RateLimitMiddleware


class RateLimitMiddlewareTest(TestCase):
    """Test rate limiting behavior."""

    def setUp(self):
        self.factory = RequestFactory()
        self.mock_redis = MagicMock()

        self._redis_patcher = patch("nexusops.ratelimit.redis_lib")
        self._redis_mod = self._redis_patcher.start()
        self._redis_mod.from_url.return_value = self.mock_redis

    def tearDown(self):
        self._redis_patcher.stop()

    def _make_middleware(self, view_fn):
        return RateLimitMiddleware(view_fn)

    def _call(self, path="/api/v1/jobs/", ip="127.0.0.1"):
        request = self.factory.get(path, REMOTE_ADDR=ip)
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        return mw(request), view_fn

    def test_first_request_allowed(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        response, view_fn = self._call()
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-RateLimit-Limit", response)

    def test_bucket_initialized_on_first_request(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        self._call()
        pipe = self.mock_redis.pipeline.return_value
        pipe.hset.assert_called_once()

    def test_rate_limit_headers_present(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        response, _ = self._call()
        self.assertIn("X-RateLimit-Limit", response)
        self.assertIn("X-RateLimit-Remaining", response)
        self.assertIn("X-RateLimit-Category", response)

    def test_api_category_detected(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        response, _ = self._call("/api/v1/jobs/")
        self.assertEqual(response["X-RateLimit-Category"], "api")

    def test_page_category_detected(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        response, _ = self._call("/")
        self.assertEqual(response["X-RateLimit-Category"], "pages")

    def test_health_category_detected(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        response, _ = self._call("/health")
        self.assertEqual(response["X-RateLimit-Category"], "health")

    def test_redis_error_fails_open(self):
        self.mock_redis.pipeline.return_value.execute.side_effect = Exception("Redis down")
        response, view_fn = self._call()
        self.assertEqual(response.status_code, 200)
        view_fn.assert_called_once()

    def test_returns_429_when_bucket_empty(self):
        now = time.time()
        self.mock_redis.pipeline.return_value.execute.side_effect = [
            [{"tokens": "0.0", "last_refill": str(now)}],
        ]
        response, _ = self._call("/api/v1/jobs/")
        self.assertEqual(response.status_code, 429)

    def test_429_has_retry_after_header(self):
        now = time.time()
        self.mock_redis.pipeline.return_value.execute.side_effect = [
            [{"tokens": "0.0", "last_refill": str(now)}],
        ]
        response, _ = self._call("/api/v1/jobs/")
        self.assertIn("Retry-After", response)

    def test_429_has_rate_limit_body(self):
        now = time.time()
        self.mock_redis.pipeline.return_value.execute.side_effect = [
            [{"tokens": "0.0", "last_refill": str(now)}],
        ]
        response, _ = self._call("/api/v1/jobs/")
        self.assertIn(b"rate_limit_exceeded", response.content)

    def test_existing_bucket_consuming_token(self):
        now = time.time()
        max_tokens = RATE_LIMITS["api"][0]
        self.mock_redis.pipeline.return_value.execute.side_effect = [
            [{"tokens": str(max_tokens), "last_refill": str(now - 5)}],
        ]
        response, _ = self._call("/api/v1/jobs/")
        self.assertEqual(response.status_code, 200)

    def test_client_ip_from_xff(self):
        self.mock_redis.pipeline.return_value.execute.return_value = [{}]
        request = self.factory.get("/api/v1/jobs/")
        request.META["HTTP_X_FORWARDED_FOR"] = "10.0.0.1, 10.0.0.2"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        response = mw(request)
        self.assertEqual(response.status_code, 200)

    def test_api_limit_config(self):
        max_tokens, refill = RATE_LIMITS["api"]
        self.assertGreater(max_tokens, 0)
        self.assertGreater(refill, 0)

    def test_pages_limit_config(self):
        max_tokens, refill = RATE_LIMITS["pages"]
        self.assertGreater(max_tokens, 0)
        self.assertGreater(refill, 0)

    def test_health_limit_config(self):
        max_tokens, refill = RATE_LIMITS["health"]
        self.assertGreater(max_tokens, 0)
        self.assertGreater(refill, 0)

    def test_token_refill_prevents_429(self):
        now = time.time()
        max_tokens = RATE_LIMITS["api"][0]
        self.mock_redis.pipeline.return_value.execute.side_effect = [
            [{"tokens": str(max_tokens - 1), "last_refill": str(now - 10)}],
        ]
        response, _ = self._call("/api/v1/jobs/")
        self.assertEqual(response.status_code, 200)
