"""nexusops URL Configuration."""
from django.contrib import admin
from django.urls import include, path

from apps.executions.dashboard import dashboard_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", dashboard_view, name="dashboard"),
    path("api/v1/", include("apps.jobs.urls")),
    path("api/v1/", include("apps.executions.urls")),
]
