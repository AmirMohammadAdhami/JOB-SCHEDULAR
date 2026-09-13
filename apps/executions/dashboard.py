"""Dashboard view — serves the HTML dashboard page."""
from django.shortcuts import render


def dashboard_view(request):
    """Render the dashboard page."""
    return render(request, "dashboard.html")
