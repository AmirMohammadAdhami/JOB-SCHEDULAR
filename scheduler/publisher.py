"""
Outbox publisher — polls PENDING events and sends them to RabbitMQ.

This implements the Outbox Pattern:
1. Scheduler creates records + outbox event in ONE transaction
2. Publisher polls for PENDING events
3. Publisher sends to RabbitMQ
4. On success: mark as PUBLISHED
5. On failure: increment attempts, schedule retry with backoff
6. After max failures: mark as FAILED (manual intervention needed)

The database and RabbitMQ are eventually consistent.
If RabbitMQ is down, events stay PENDING and are retried.
"""
import json
import logging

from django.db import transaction
from django.utils import timezone as tz
import pika

from apps.executions.models import OutboxEvent
from scheduler.rabbitmq import (
    RabbitMQConnection,
    get_queue_for_priority,
    setup_queues,
)

logger = logging.getLogger("scheduler.publisher")

# Configuration
POLL_INTERVAL_SECONDS = 1
BATCH_SIZE = 50
MAX_PUBLISH_ATTEMPTS = 10
BACKOFF_BASE_SECONDS = 1


def calculate_backoff(attempt: int) -> float:
    """Calculate exponential backoff with jitter."""
    import random
    delay = BACKOFF_BASE_SECONDS * (2 ** min(attempt, 10))
    jitter = random.uniform(0, delay * 0.1)
    return float(delay + jitter)


def publish_event(channel: pika.channel.Channel, event: OutboxEvent) -> bool:
    """
    Publish a single event to RabbitMQ.

    Returns True on success, False on failure.
    """
    try:
        queue_name = get_queue_for_priority(
            event.payload.get("priority", "MEDIUM")
        )

        # Publish with persistent delivery mode
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps(event.payload, default=str),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Persistent
                content_type="application/json",
                message_id=str(event.id),
            ),
        )

        logger.debug(
            f"Published event {event.id} to {queue_name}: "
            f"{event.event_type}"
        )
        return True

    except pika.exceptions.AMQPConnectionError as e:
        logger.warning(f"RabbitMQ connection lost: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to publish event {event.id}: {e}")
        return False


def publish_pending_events() -> int:
    """
    Poll for PENDING events and publish them to RabbitMQ.

    Returns the number of events published.
    """
    published_count = 0

    try:
        rabbitmq = RabbitMQConnection()
        rabbitmq.connect()
        channel = rabbitmq.get_channel()
        setup_queues(channel)
    except Exception as e:
        logger.warning(f"Cannot connect to RabbitMQ: {e}")
        return 0

    try:
        with transaction.atomic():
            # Lock pending events for update
            events = list(
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(
                    status=OutboxEvent.Status.PENDING,
                    next_attempt_at__lte=tz.now(),
                )
                .order_by("created_at")
                [:BATCH_SIZE]
            )

            if not events:
                return 0

            logger.debug(f"Found {len(events)} pending events")

            for event in events:
                success = publish_event(channel, event)

                if success:
                    # Mark as published
                    event.status = OutboxEvent.Status.PUBLISHED
                    event.published_at = tz.now()
                    event.save(update_fields=[
                        "status",
                        "published_at",
                    ])
                    published_count += 1
                else:
                    # Increment attempts and schedule retry
                    event.attempts += 1

                    if event.attempts >= MAX_PUBLISH_ATTEMPTS:
                        event.status = OutboxEvent.Status.FAILED
                        logger.error(
                            f"Event {event.id} failed after "
                            f"{MAX_PUBLISH_ATTEMPTS} attempts"
                        )
                    else:
                        backoff = calculate_backoff(event.attempts)
                        event.next_attempt_at = (
                            tz.now() + tz.timedelta(seconds=backoff)
                        )

                    event.save(update_fields=[
                        "status",
                        "attempts",
                        "next_attempt_at",
                    ])

    except Exception as e:
        logger.error(f"Publish batch failed: {e}", exc_info=True)
    finally:
        try:
            rabbitmq.close()
        except Exception:
            pass

    if published_count > 0:
        logger.info(f"Published {published_count} events to RabbitMQ")

    return published_count
