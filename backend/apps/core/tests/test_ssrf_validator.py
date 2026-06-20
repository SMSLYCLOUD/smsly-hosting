import pytest
from apps.core.validators import validate_ssrf
from django.core.exceptions import ValidationError


def test_ssrf_valid_paths():
    validate_ssrf('/health')
    validate_ssrf('/api/v1/status')
    validate_ssrf('https://api.github.com/status')
    validate_ssrf('status')

def test_ssrf_blocked_hosts():
    with pytest.raises(ValidationError, match="not allowed"):
        validate_ssrf('http://localhost:8080/health')
    with pytest.raises(ValidationError, match="not allowed"):
        validate_ssrf('http://metadata.google.internal/computeMetadata/v1/')
    with pytest.raises(ValidationError, match="not allowed"):
        validate_ssrf('http://169.254.169.254/latest/meta-data/')

def test_ssrf_blocked_ips():
    with pytest.raises(ValidationError, match="not allowed"):
        validate_ssrf('http://127.0.0.1/admin')
    with pytest.raises(ValidationError, match="Private"):
        validate_ssrf('http://10.0.0.5/internal')
    with pytest.raises(ValidationError, match="Private"):
        validate_ssrf('http://192.168.1.100/health')
    with pytest.raises(ValidationError, match="Private"):
        validate_ssrf('http://172.16.0.5/metrics')

def test_ssrf_ipv6():
    with pytest.raises(ValidationError, match="not allowed"):
        validate_ssrf('http://[::1]/admin')
    with pytest.raises(ValidationError, match="Loopback"):
        validate_ssrf('http://[0:0:0:0:0:0:0:1]/admin')
