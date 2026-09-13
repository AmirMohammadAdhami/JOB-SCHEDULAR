"""nexusops URL Configuration."""
from django.contrib import admin
from django.urls import include, path

from apps.executions.dashboard import (
    dashboard_view,
    jobs_view,
    runs_view,
    workers_view,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", dashboard_view, name="dashboard"),
    path("jobs", jobs_view, name="jobs"),
    path("runs", runs_view, name="runs"),
    path("workers", workers_view, name="workers"),
    path("api/v1/", include("apps.jobs.urls")),
    path("api/v1/", include("apps.executions.urls")),
]
