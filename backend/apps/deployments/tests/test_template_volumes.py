# pylint: disable=invalid-name
"""Template volume auto-mounting (read-only rootfs compat + fixture audit).

Regression (2026-09-25): the templates.json ``volumes`` field existed
but nothing consumed it, so every template needing a writable path
(PocketBase /pb_data, …) crash-looped with ``mkdir: read-only file
system`` under the platform's read-only rootfs.

Security model (see _classify_template_volume): auto-creation only
ever makes Docker NAMED volumes (isolated daemon storage). Explicit
bind specs and host-privileged paths are refused — a fixture edit
can never graft host paths like docker.sock into a container.
"""
import json
import os

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.deployments.models import Service
from apps.deployments.models.storage import Volume
from apps.deployments.tasks.deployment.tasks_templates import (
    _classify_template_volume,
    _ensure_template_volumes,
)


class ClassifyTemplateVolumeTests(SimpleTestCase):
    def test_plain_container_path_becomes_named_volume(self):
        action, path = _classify_template_volume("/pb_data")
        self.assertEqual((action, path), ("named", "/pb_data"))

    def test_nested_app_paths_allowed(self):
        for raw in ("/usr/app/data", "/var/lib/postgresql/data", "/data/db"):
            action, path = _classify_template_volume(raw)
            self.assertEqual(action, "named", raw)
            self.assertEqual(path, raw)

    def test_explicit_bind_specs_refused(self):
        for raw in (
            "/var/run/docker.sock:/var/run/docker.sock",
            "/etc/timezone:/etc/timezone:ro",
            "mydata:/data",
        ):
            action, _ = _classify_template_volume(raw)
            self.assertEqual(action, "skip", raw)

    def test_host_privileged_paths_refused(self):
        for raw in ("/var/run/docker.sock", "/proc/self", "/sys/kernel",
                    "/dev/null", "/boot/x", "/var/run/a", "/etc/passwd",
                    "/etc/shadow", "/etc/ssh/sshd_config"):
            action, _ = _classify_template_volume(raw)
            self.assertEqual(action, "skip", raw)

    def test_app_config_dirs_allowed_as_named_volumes(self):
        # Named volumes never touch the host: the container path is
        # just a mountpoint in its own namespace.
        for raw in ("/etc/gitlab", "/root/.ollama", "/var/log/gitlab",
                    "/var/opt/gitlab", "/pb_data"):
            action, path = _classify_template_volume(raw)
            self.assertEqual(action, "named", raw)
            self.assertEqual(path, raw)

    def test_traversal_and_garbage_refused(self):
        for raw in ("", "relative/path", "/a/../../etc", "/"):
            action, _ = _classify_template_volume(raw)
            self.assertEqual(action, "skip", raw)

    def test_dict_entries_supported(self):
        self.assertEqual(
            _classify_template_volume({"path": "/pb_data"}),
            ("named", "/pb_data"),
        )
        self.assertEqual(
            _classify_template_volume({"mount_path": "/x"})[0], "named",
        )


class TemplateFixtureAuditTests(SimpleTestCase):
    """Every fixture entry must classify cleanly: named volumes pass
    the safety rules, and nothing honourable is silently dropped."""

    @classmethod
    def _entries(cls):
        base = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "templates.json",
        )
        with open(base, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = data.get("templates", [])
        return data

    def test_all_fixture_volumes_classify(self):
        from apps.deployments.signals.validation import (
            _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES,
        )

        def _signal_accepts(path: str) -> bool:
            return any(
                path == prefix.rstrip("/") or path.startswith(prefix)
                for prefix in _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES
            )

        named = 0
        for template in self._entries():
            if not isinstance(template, dict):
                continue
            for raw in template.get("volumes") or []:
                action, payload = _classify_template_volume(raw)
                if action == "named":
                    named += 1
                    # Single source of truth: every auto-mounted path
                    # must satisfy the DB backstop, or the deploy would
                    # fail at save time.
                    self.assertTrue(
                        _signal_accepts(payload),
                        f"{template.get('id')}:{raw!r} -> {payload!r} "
                        f"rejected by volume mount gate",
                    )
                else:
                    # Skips must be explicit bind specs only — a safety
                    # rejection of a plain app path means the classifier
                    # and the gate have drifted.
                    self.assertIn(
                        "bind spec", payload,
                        f"{template.get('id')}:{raw!r} skipped for a non-bind "
                        f"reason: {payload}",
                    )
        # The catalog must actually produce volumes (else the feature
        # is dead).
        self.assertGreater(named, 10)


class EnsureTemplateVolumesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tmpl_vol_admin", password="123")
        self.service = Service.objects.create(name="tmpl-vol-svc", owner=self.user)

    def test_creates_rows_and_is_idempotent(self):
        template = {"id": "pocketbase", "volumes": ["/pb_data"]}
        first = _ensure_template_volumes(self.service, template)
        self.assertEqual(len(first["created"]), 1)
        self.assertEqual(first["skipped"], [])
        name, path = first["created"][0]
        self.assertEqual(path, "/pb_data")
        row = Volume.objects.get(service=self.service, mount_path="/pb_data")
        self.assertEqual(row.name, name)
        second = _ensure_template_volumes(self.service, template)
        self.assertEqual(second["created"], [])
        self.assertEqual(Volume.objects.filter(service=self.service).count(), 1)

    def test_none_template_is_noop(self):
        self.assertEqual(
            _ensure_template_volumes(self.service, None),
            {"created": [], "skipped": []},
        )
