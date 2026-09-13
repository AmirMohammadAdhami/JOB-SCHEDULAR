"""nexusops URL Configuration."""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from apps.core.health import health_live, health_ready
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
    # Health checks
    path("health", health_live, name="health-live"),
    path("health/ready", health_ready, name="health-ready"),
    # API documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
