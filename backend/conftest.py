import pytest

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

