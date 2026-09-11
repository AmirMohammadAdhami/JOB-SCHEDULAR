import os

# pytest-django handles Django setup via DJANGO_SETTINGS_MODULE in pytest.ini
# This conftest only sets the env var as a fallback
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexusops.settings")
