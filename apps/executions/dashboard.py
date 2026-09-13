"""Dashboard views — serves the HTML pages."""
from django.shortcuts import render


def dashboard_view(request):
    """Render the dashboard page."""
    return render(request, "dashboard.html", {"active_page": "dashboard"})


def jobs_view(request):
    """Render the jobs management page."""
    return render(request, "jobs.html", {"active_page": "jobs"})


def runs_view(request):
    """Render the runs history page."""
    return render(request, "runs.html", {"active_page": "runs"})


def workers_view(request):
    """Render the workers page."""
    return render(request, "workers.html", {"active_page": "workers"})
