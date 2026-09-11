import os

import pytest

# Ensure Django settings are configured before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexusops.settings")


@pytest.fixture(scope="session", autouse=True)
def django_settings():
    """Ensure Django is set up for the test session."""
    import django
    django.setup()
