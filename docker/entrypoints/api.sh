#!/bin/bash
set -e

echo "Starting Django API server..."
exec python manage.py runserver 0.0.0.0:8000
