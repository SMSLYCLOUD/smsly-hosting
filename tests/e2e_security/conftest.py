"""Shared pytest fixtures and isolated Django bootstrap for tests/e2e_security/."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("IS_TESTING", "1")
os.environ.setdefault("SMSLY_DISABLE_STARTUP_TASKS", "true")
os.environ.setdefault("DEBUG", "False")
os.environ.setdefault("SMSLY_DISABLE_SIGNATURE_CHECK", "False")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-e2e-security-suite-only")
os.environ.setdefault(
    "FIELD_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)

django.setup()


@pytest.fixture(autouse=True)
def _isolate_e2e_environment(settings):
    """Keep HMAC enforcement active and flush LocMemCache + PlatformConfig singleton."""
    settings.SMSLY_DISABLE_SIGNATURE_CHECK = False
    settings.DEBUG = False

    from django.core.cache import cache
    from apps.deployments.models.platform import PlatformConfig

    cache.clear()
    PlatformConfig.clear_cache()
    try:
        yield
    finally:
        cache.clear()
        PlatformConfig.clear_cache()


@pytest.fixture
def disable_signature_check(settings):
    """Explicit opt-out fixture for tests that disable Gateway HMAC verification."""
    settings.SMSLY_DISABLE_SIGNATURE_CHECK = True
    return settings


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.create_user(
        username="e2e-user",
        email="e2e-user@example.test",
        password="testpass123",
    )


@pytest.fixture
def staff_user(db):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.create_user(
        username="e2e-staff",
        email="e2e-staff@example.test",
        password="testpass123",
        is_staff=True,
    )


@pytest.fixture
def superuser(db):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.create_superuser(
        username="e2e-admin",
        email="e2e-admin@example.test",
        password="testpass123",
    )


def is_docker_available() -> bool:
    """Return True only when a Docker CLI & responsive daemon are available."""
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture
def docker_available() -> bool:
    return is_docker_available()


@pytest.fixture
def mock_docker_client():
    """Return a MagicMock Docker SDK client compatible with docker-py 7.x plain-dict configs."""
    client = MagicMock()
    client.ping.return_value = True
    client.info.return_value = {
        "SecurityOptions": ["name=apparmor", "name=seccomp,profile=builtin"]
    }
    client.api.create_endpoint_config.side_effect = lambda **kw: dict(kw)

    def _make_container(name: str = "mock-container", container_id: str = "abc123def4567890"):
        container = MagicMock()
        container.id = container_id
        container.name = name
        container.status = "running"
        container.attrs = {
            "State": {"Running": True, "Status": "running"},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
        }
        return container

    default_container = _make_container()
    client.containers.create.return_value = default_container
    client.containers.run.return_value = default_container
    client.containers.get.return_value = default_container
    client.containers.list.return_value = []

    default_net = MagicMock()
    default_net.name = "smsly-net-mock"
    default_net.attrs = {
        "Id": "0123456789abcdef0123456789abcdef",
        "IPAM": {"Config": [{"Subnet": "172.30.224.0/24"}]},
    }
    client.networks.get.return_value = default_net
    client.networks.create.return_value = default_net
    client.networks.list.return_value = []

    return client
