"""
RabbitMQ connection manager.

Handles connection lifecycle, reconnection, and channel management.
Uses pika's BlockingConnection for simplicity in the scheduler process.
"""
import logging
import os
import pika

logger = logging.getLogger("scheduler.rabbitmq")

# RabbitMQ connection params from environment
RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
)

# Queue names
QUEUE_CRITICAL = "jobs_critical"
QUEUE_HIGH = "jobs_high"
QUEUE_MEDIUM = "jobs_medium"
QUEUE_LOW = "jobs_low"


# Priority mapping
PRIORITY_TO_QUEUE = {
    "CRITICAL": QUEUE_CRITICAL,
    "HIGH": QUEUE_HIGH,
    "MEDIUM": QUEUE_MEDIUM,
    "LOW": QUEUE_LOW,

}

# All queues for setup
ALL_QUEUES = [QUEUE_CRITICAL, QUEUE_HIGH, QUEUE_MEDIUM, QUEUE_LOW]


class RabbitMQConnection:
    """Manages a single RabbitMQ connection with auto-reconnect."""

    def __init__(self, url: str = RABBITMQ_URL):
        self._url = url
        self._connection = None
        self._channel = None

    def connect(self) -> pika.BlockingConnection:
        """Establish connection to RabbitMQ."""
        if self._connection and self._connection.is_open:
            return self._connection

        params = pika.URLParameters(self._url)
        params.socket_timeout = 5
        params.connection_attempts = 3
        params.retry_delay = 2

        try:
            self._connection = pika.BlockingConnection(params)
            self._channel = self._connection.channel()
            logger.info("Connected to RabbitMQ")
            return self._connection
        except pika.exceptions.AMQPConnectionError as e:
            logger.warning(f"RabbitMQ connection failed: {e}")
            raise

    def get_channel(self) -> pika.channel.Channel:
        """Get a channel, reconnecting if necessary."""
        if not self._connection or self._connection.is_closed:
            self.connect()
        return self._channel

    def close(self):
        """Close the connection."""
        try:
            if self._connection and self._connection.is_open:
                self._connection.close()
                logger.info("RabbitMQ connection closed")
        except Exception as e:
            logger.warning(f"Error closing RabbitMQ connection: {e}")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def setup_queues(channel: pika.channel.Channel):
    """Declare all priority queues."""
    for queue_name in ALL_QUEUES:
        channel.queue_declare(
            queue=queue_name,
            durable=True,
        )
        logger.debug(f"Declared queue: {queue_name}")


def get_queue_for_priority(priority: str) -> str:
    """Map a job priority to a queue name."""
    return PRIORITY_TO_QUEUE.get(priority, QUEUE_MEDIUM)
