import os
import sys

import django
import pytest

# Ensure backend is on the path
BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

# Django setup
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


# ---------------------------------------------------------------------------
# Autouse fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_test_environment(settings):
    """Disable HMAC signature checks and reset caches for all tests."""
    settings.SMSLY_DISABLE_SIGNATURE_CHECK = True
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Shared user fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
    )


# ---------------------------------------------------------------------------
# DRF API client
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


# ---------------------------------------------------------------------------
# Mock Docker client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_docker():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.ping.return_value = True
    return client
