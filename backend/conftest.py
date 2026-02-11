import pytest

@pytest.fixture(autouse=True)
def disable_signature_check(settings):
    """
    Disable HMAC signature check for all tests.
    """
    settings.SMSLY_DISABLE_SIGNATURE_CHECK = True
