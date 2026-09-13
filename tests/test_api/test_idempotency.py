"""
Tests for idempotency key middleware.
"""
import json
from unittest.mock import MagicMock, patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from nexusops.middleware import IdempotencyMiddleware


class IdempotencyMiddlewareTest(TestCase):
    """Test idempotency key behavior."""

    def setUp(self):
        self.factory = RequestFactory()
        self.mock_redis = MagicMock()
        self.mock_redis.get.return_value = None

        self._redis_patcher = patch("nexusops.middleware.redis_lib")
        self._redis_mod = self._redis_patcher.start()
        self._redis_mod.from_url.return_value = self.mock_redis

    def tearDown(self):
        self._redis_patcher.stop()

    def _make_middleware(self, view_fn):
        return IdempotencyMiddleware(view_fn)

    def _call(self, method, path="/api/v1/jobs/", idempotency_key=None):
        request = getattr(self.factory, method.lower())(path)
        request.user_id = None
        if idempotency_key:
            request.META["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        return mw(request), view_fn

    def test_no_key_passes_through(self):
        response, view_fn = self._call("POST")
        self.assertEqual(response.status_code, 200)
        view_fn.assert_called_once()

    def test_get_request_ignored(self):
        request = self.factory.get("/api/v1/jobs/")
        request.META["HTTP_IDEMPOTENCY_KEY"] = "test-key-123"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        response = mw(request)
        self.assertEqual(response.status_code, 200)
        view_fn.assert_called_once()

    def test_first_request_stores_response(self):
        response, view_fn = self._call("POST", idempotency_key="key-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mock_redis.setex.call_count, 1)

    def test_cached_request_returns_stored_response(self):
        stored_data = json.dumps({
            "body": '{"run_id": "123"}',
            "status": 201,
            "content_type": "application/json",
            "headers": {},
            "timestamp": 1000.0,
        })
        self.mock_redis.get.return_value = stored_data

        request = self.factory.post("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "cached-key"
        view_fn = MagicMock(return_value=HttpResponse("new", status=200))
        mw = self._make_middleware(view_fn)
        response = mw(request)

        self.assertEqual(response.status_code, 201)
        self.assertIn(b'{"run_id": "123"}', response.content)
        self.assertEqual(response["X-Idempotent-Replay"], "true")
        view_fn.assert_not_called()

    def test_non_success_not_cached(self):
        request = self.factory.post("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "error-key"
        view_fn = MagicMock(return_value=HttpResponse("error", status=400))
        mw = self._make_middleware(view_fn)
        mw(request)
        self.assertEqual(self.mock_redis.setex.call_count, 0)

    def test_cache_key_includes_key(self):
        self._call("POST", idempotency_key="key-unique")
        call_args = self.mock_redis.setex.call_args
        cache_key = call_args[0][0]
        self.assertIn("idempotency", cache_key)
        self.assertIn("key-unique", cache_key)

    def test_put_request_is_idempotent(self):
        request = self.factory.put("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "put-key"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        mw(request)
        self.assertEqual(self.mock_redis.setex.call_count, 1)

    def test_redis_error_fails_open(self):
        self.mock_redis.get.side_effect = Exception("Redis down")
        request = self.factory.post("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "fail-key"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        response = mw(request)
        self.assertEqual(response.status_code, 200)
        view_fn.assert_called_once()

    def test_ttl_is_24_hours(self):
        self._call("POST", idempotency_key="ttl-key")
        call_args = self.mock_redis.setex.call_args
        ttl = call_args[0][1]
        self.assertEqual(ttl, 86400)

    def test_patch_request_is_idempotent(self):
        request = self.factory.patch("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "patch-key"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        mw(request)
        self.assertEqual(self.mock_redis.setex.call_count, 1)

    def test_delete_request_is_idempotent(self):
        request = self.factory.delete("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "delete-key"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        mw(request)
        self.assertEqual(self.mock_redis.setex.call_count, 1)

    def test_cache_store_error_does_not_crash(self):
        self.mock_redis.setex.side_effect = Exception("Redis write failed")
        request = self.factory.post("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "write-fail-key"
        view_fn = MagicMock(return_value=HttpResponse("ok", status=200))
        mw = self._make_middleware(view_fn)
        response = mw(request)
        self.assertEqual(response.status_code, 200)
        view_fn.assert_called_once()

    def test_stored_response_preserves_content_type(self):
        stored_data = json.dumps({
            "body": '{"ok": true}',
            "status": 200,
            "content_type": "application/json",
            "headers": {"X-Custom": "yes"},
            "timestamp": 1000.0,
        })
        self.mock_redis.get.return_value = stored_data

        request = self.factory.post("/api/v1/jobs/")
        request.user_id = None
        request.META["HTTP_IDEMPOTENCY_KEY"] = "ct-key"
        view_fn = MagicMock(return_value=HttpResponse("new", status=200))
        mw = self._make_middleware(view_fn)
        response = mw(request)

        self.assertEqual(response["X-Custom"], "yes")
