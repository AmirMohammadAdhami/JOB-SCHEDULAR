"""
Health check endpoints for load balancers and orchestrators.

Endpoints:
- GET /health         — Liveness check (is process alive?)
- GET /health/ready   — Readiness check (can serve traffic?)
"""
import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger("apps.core.health")


@require_GET
def health_live(request):
    """Liveness — confirms the process is running."""
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request):
    """Readiness — confirms DB, Redis, and RabbitMQ are reachable."""
    checks = {}

    # PostgreSQL
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as e:
        logger.error("Health check failed: postgres — %s", e)
        checks["postgres"] = f"error: {e}"

    # Redis
    try:
        import redis as redis_lib
        from django.conf import settings
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.error("Health check failed: redis — %s", e)
        checks["redis"] = f"error: {e}"

    # RabbitMQ (via pika)
    try:
        import pika
        from django.conf import settings
        params = pika.URLParameters(settings.RABBITMQ_URL)
        params.socket_timeout = 2
        conn = pika.BlockingConnection(params)
        conn.close()
        checks["rabbitmq"] = "ok"
    except Exception as e:
        logger.error("Health check failed: rabbitmq — %s", e)
        checks["rabbitmq"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JsonResponse(
        {"status": "ok" if all_ok else "degraded", "checks": checks},
        status=status_code,
    )
