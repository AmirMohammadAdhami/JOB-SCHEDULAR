"""nexusops URL Configuration."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.jobs.urls")),
    path("api/v1/", include("apps.executions.urls")),
]
