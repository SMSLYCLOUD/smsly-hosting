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


@pytest.fixture(autouse=True)
def disable_signature_check(settings):
    """
    Disable HMAC signature check for all tests.
    """
    settings.SMSLY_DISABLE_SIGNATURE_CHECK = True


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    """
    Reset DRF throttle state between tests so the 3/minute burst
    and 10/hour deployment throttles don't leak across test cases.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _isolate_test_environment(settings):
    """Disable HMAC signature checks for all tests by default.
    Tests that need to verify HMAC behavior should explicitly
    override settings.SMSLY_DISABLE_SIGNATURE_CHECK = False."""
    settings.SMSLY_DISABLE_SIGNATURE_CHECK = True
