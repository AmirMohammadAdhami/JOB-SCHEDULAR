"""
Idempotency middleware — prevents duplicate mutations on retries.

How it works:
- Client sends `Idempotency-Key` header on POST/PUT/PATCH/DELETE requests.
- Middleware stores key + response in Redis with a TTL (24h).
- On retry with same key, returns the stored response.
- Keys are scoped per-user (or per-IP for anonymous).

Redis keys: `idempotency:{key}:{user_id}` → JSON blob.
"""
import json
import logging
import time
from typing import Optional

import redis as redis_lib
from django.conf import settings
from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class IdempotencyMiddleware:
    """
    Enforce idempotency for state-mutating requests.

    If a client sends an `Idempotency-Key` header:
      - First request: process normally, store response body + status.
      - Subsequent requests (within TTL): return stored response, skip processing.

    If no header is sent, the request passes through unchanged.
    """

    IDEMPOTENT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    TTL_SECONDS = 86400  # 24 hours
    PREFIX = "idempotency"

    def __init__(self, get_response):
        self.get_response = get_response
        self._redis = None

    @property
    def redis(self):
        if self._redis is None:
            self._redis = redis_lib.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        return self._redis

    def __call__(self, request: HttpRequest) -> HttpResponse:
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key or request.method not in self.IDEMPOTENT_METHODS:
            return self.get_response(request)

        cache_key = self._build_cache_key(idempotency_key, request)
        cached = self._get_cached_response(cache_key)
        if cached is not None:
            logger.info("Idempotency hit: key=%s path=%s", idempotency_key, request.path)
            return cached

        response = self.get_response(request)

        if 200 <= response.status_code < 300:
            self._store_response(cache_key, response)
            logger.info(
                "Idempotency stored: key=%s path=%s status=%d",
                idempotency_key,
                request.path,
                response.status_code,
            )
        else:
            logger.debug(
                "Idempotency skipped (non-success): key=%s status=%d",
                idempotency_key,
                response.status_code,
            )

        return response

    def _build_cache_key(self, key: str, request: HttpRequest) -> str:
        user_id = getattr(request, "user_id", None) or "anonymous"
        ip = request.META.get("REMOTE_ADDR", "unknown")
        return f"{self.PREFIX}:{key}:{user_id}:{ip}"

    def _get_cached_response(self, cache_key: str) -> Optional[HttpResponse]:
        try:
            data = self.redis.get(cache_key)
            if data is None:
                return None
            stored = json.loads(data)
            response = HttpResponse(
                content=stored["body"],
                status=stored["status"],
                content_type=stored.get("content_type", "application/json"),
            )
            for header, value in stored.get("headers", {}).items():
                response[header] = value
            response["X-Idempotent-Replay"] = "true"
            return response
        except Exception:
            logger.exception("Failed to get cached idempotent response")
            return None

    def _store_response(self, cache_key: str, response: HttpResponse) -> None:
        try:
            body = response.content.decode("utf-8") if isinstance(response.content, bytes) else response.content
            data = {
                "body": body,
                "status": response.status_code,
                "content_type": response.get("Content-Type", "application/json"),
                "headers": {
                    k: v for k, v in response.items()
                    if k.lower() not in ("content-type",)
                },
                "timestamp": time.time(),
            }
            self.redis.setex(cache_key, self.TTL_SECONDS, json.dumps(data))
        except Exception:
            logger.exception("Failed to store idempotent response")
