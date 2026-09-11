#!/bin/bash
set -e

echo "Waiting for PostgreSQL..."
while ! python -c "import psycopg2; psycopg2.connect('$DATABASE_URL')" 2>/dev/null; do
    echo "  PostgreSQL not ready, waiting 2s..."
    sleep 2
done
echo "PostgreSQL is ready."

echo "Waiting for RabbitMQ..."
# Simple check: try to connect to port 5672
while ! python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(('rabbitmq', 5672))
    s.close()
except:
    raise
" 2>/dev/null; do
    echo "  RabbitMQ not ready, waiting 2s..."
    sleep 2
done
echo "RabbitMQ is ready."

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Starting Scheduler..."
exec python -m scheduler.main
