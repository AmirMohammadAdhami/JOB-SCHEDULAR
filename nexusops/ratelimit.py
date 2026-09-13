"""
Rate limiting middleware — token bucket algorithm via Redis.

How it works:
- Each client (by IP) gets a token bucket.
- Requests consume tokens; tokens refill at a fixed rate.
- If no tokens remain, return 429 Too Many Requests.
- Configurable per-endpoint limits (API vs pages vs health).

Redis keys: `ratelimit:{scope}:{identifier}` → `{tokens, last_refill}`.
"""
import logging
import time
from typing import Optional

import redis as redis_lib
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

# Rate limit configurations: (max_tokens, refill_per_second)
RATE_LIMITS = {
    "api": (100, 1.0),       # 100 requests/second, refills 1 token/sec
    "pages": (60, 1.0),       # 60 requests/second
    "health": (300, 5.0),     # 300 requests/second
}

# Endpoint → rate limit category mapping
ENDPOINT_CATEGORIES = {
    "/api/v1/": "api",
    "/api/": "api",
    "/health": "health",
}

# Default for page endpoints (/, /jobs, /runs, /workers)
DEFAULT_CATEGORY = "pages"


class RateLimitMiddleware:
    """
    Token bucket rate limiter using Redis.

    Each client IP gets a bucket with configurable burst capacity.
    Tokens refill continuously at the configured rate.
    Exceeding the limit returns 429 with Retry-After header.
    """

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
        identifier = self._get_client_id(request)
        category = self._get_category(request.path)
        max_tokens, refill_rate = RATE_LIMITS[category]

        allowed, remaining = self._check_rate_limit(identifier, category, max_tokens, refill_rate)

        if not allowed:
            retry_after = max(1, int(1 / refill_rate))
            logger.warning(
                "Rate limit exceeded: ip=%s path=%s category=%s",
                identifier, request.path, category,
            )
            return JsonResponse(
                {
                    "error": "rate_limit_exceeded",
                    "detail": f"Rate limit exceeded for {category} endpoints.",
                    "retry_after": retry_after,
                },
                status=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_tokens),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Category": category,
                },
            )

        response = self.get_response(request)

        response["X-RateLimit-Limit"] = str(max_tokens)
        response["X-RateLimit-Remaining"] = str(remaining)
        response["X-RateLimit-Category"] = category

        return response

    def _get_client_id(self, request: HttpRequest) -> str:
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")

    def _get_category(self, path: str) -> str:
        for prefix, category in ENDPOINT_CATEGORIES.items():
            if path.startswith(prefix):
                return category
        return DEFAULT_CATEGORY

    def _check_rate_limit(
        self, identifier: str, category: str, max_tokens: int, refill_rate: float
    ) -> tuple[bool, int]:
        """
        Token bucket algorithm.

        Returns (allowed: bool, remaining: int).
        """
        now = time.time()
        key = f"ratelimit:{category}:{identifier}"

        try:
            pipe = self.redis.pipeline()
            pipe.hgetall(key)
            result = pipe.execute()[0]

            if not result:
                # First request — initialize bucket
                tokens = max_tokens - 1
                pipe.hset(key, mapping={"tokens": tokens, "last_refill": now})
                pipe.expire(key, 300)  # 5 min TTL
                pipe.execute()
                return True, tokens

            current_tokens = float(result.get("tokens", max_tokens))
            last_refill = float(result.get("last_refill", now))

            # Refill tokens based on elapsed time
            elapsed = now - last_refill
            new_tokens = min(max_tokens, current_tokens + elapsed * refill_rate)

            if new_tokens < 1:
                return False, 0

            # Consume one token
            new_tokens -= 1
            remaining = int(new_tokens)

            pipe.hset(key, mapping={"tokens": new_tokens, "last_refill": now})
            pipe.expire(key, 300)
            pipe.execute()

            return True, remaining

        except Exception:
            logger.exception("Rate limit check failed — allowing request")
            return True, max_tokens
