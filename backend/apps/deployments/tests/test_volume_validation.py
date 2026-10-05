"""Volume validation layer-consistency tests.

Three layers guard Volume rows (serializer -> model clean() ->
pre_save signal) with intentionally different strictness, but they
must never CONTRADICT: anything the tenant serializer accepts must
survive model clean and pre_save, and forbidden paths must die at
every layer. (2026-10-05: model clean() imported validators from a
nonexistent module so it always exploded; bare "/var/lib/smsly"
passed serializer+model but died at pre_save.)
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework import serializers

from apps.deployments.models import Service
from apps.deployments.models.storage import Volume
from apps.deployments.signals.validation import (
    _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES,
)
from apps.deployments.views.storage import (
    _VOLUME_ALLOWED_ROOTS,
    _validate_volume_mount_path,
    _validate_volume_name,
)


def _signal_allows(path: str) -> bool:
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for prefix in _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES
    )


class VolumeLayerConsistencyTests(TestCase):
    def test_narrow_roots_survive_all_layers(self):
        for root in _VOLUME_ALLOWED_ROOTS:
            for path in (root, root.rstrip("/") + "/sub"):
                with self.subTest(path=path):
                    # Serializer + model clean accept.
                    _validate_volume_mount_path(path)
                    # pre_save signal accepts.
                    self.assertTrue(
                        _signal_allows(path),
                        f"serializer-accepted {path} dies at pre_save",
                    )

    def test_forbidden_paths_die_everywhere(self):
        for path in (
            "/etc", "/etc/passwd", "/proc", "/proc/self",
            "/var/run/docker.sock", "/", "/root", "/root/.ssh",
            "/dev", "/dev/null", "/sys", "/sys/fs",
            "/var/log", "/var/log/syslog",
        ):
            with self.subTest(path=path):
                with self.assertRaises(serializers.ValidationError):
                    _validate_volume_mount_path(path)
                self.assertFalse(
                    _signal_allows(path),
                    f"forbidden {path} passes pre_save",
                )

    def test_model_clean_uses_real_validators(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username="vol-consistency", password="x")
        service = Service.objects.create(name="vol-consistency-svc", owner=user)
        row = Volume(service=service, name="data", mount_path="/data", size_gb=1)
        row.full_clean(exclude=["service"])  # must not raise
        evil = Volume(service=service, name="evil", mount_path="/etc", size_gb=1)
        with self.assertRaises(DjangoValidationError):
            evil.full_clean(exclude=["service"])

    def test_name_rules_consistent(self):
        _validate_volume_name("data-01_ok.v2")
        for bad in ("UPPER", "-lead", ".lead", "x" * 64, "smsly-evil", "postgres-x"):
            with self.subTest(name=bad):
                with self.assertRaises(serializers.ValidationError):
                    _validate_volume_name(bad)
