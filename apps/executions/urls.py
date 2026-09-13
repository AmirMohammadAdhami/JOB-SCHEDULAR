"""Executions app URL Configuration."""
from django.urls import path

from apps.executions.views import (
    CleanupOldRunsView,
    DashboardView,
    JobAttemptDetailView,
    JobAttemptListView,
    JobRunDetailView,
    JobRunListView,
    ResetStuckRunsView,
    WorkerDetailView,
    WorkerListView,
)

app_name = "executions"

urlpatterns = [
    # Runs
    path("runs/", JobRunListView.as_view(), name="run-list"),
    path("runs/<uuid:run_id>/", JobRunDetailView.as_view(), name="run-detail"),
    # Attempts
    path("attempts/", JobAttemptListView.as_view(), name="attempt-list"),
    path(
        "attempts/<uuid:attempt_id>/",
        JobAttemptDetailView.as_view(),
        name="attempt-detail",
    ),
    # Workers
    path("workers/", WorkerListView.as_view(), name="worker-list"),
    path(
        "workers/<str:worker_id>/",
        WorkerDetailView.as_view(),
        name="worker-detail",
    ),
    # Dashboard
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    # Operations
    path("ops/cleanup/", CleanupOldRunsView.as_view(), name="ops-cleanup"),
    path("ops/reset-stuck/", ResetStuckRunsView.as_view(), name="ops-reset-stuck"),
]
