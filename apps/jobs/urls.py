"""apps.jobs URL Configuration."""
from django.urls import path

from apps.jobs.views import (
    JobDetailView,
    JobListView,
    JobRunListView,
    JobTriggerView,
)

app_name = "jobs"

urlpatterns = [
    path("jobs/", JobListView.as_view(), name="job-list"),
    path("jobs/<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
    path(
        "jobs/<uuid:job_id>/trigger/",
        JobTriggerView.as_view(),
        name="job-trigger",
    ),
    path(
        "jobs/<uuid:job_id>/runs/",
        JobRunListView.as_view(),
        name="job-runs",
    ),
]
