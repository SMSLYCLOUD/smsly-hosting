"""Regression tests for compose_file path traversal validation & containment."""
import os
import tempfile
import unittest

from rest_framework import serializers

from apps.deployments.serializers.service import ServiceSerializer
from apps.deployments.services.pipeline.utils import (
    normalize_compose_file_path,
    resolve_compose_path,
)


class TestComposePathSecurity(unittest.TestCase):
    def test_valid_relative_compose_paths(self):
        self.assertEqual(normalize_compose_file_path("docker-compose.yml"), "docker-compose.yml")
        self.assertEqual(normalize_compose_file_path("./infra/compose.prod.yaml"), "infra/compose.prod.yaml")

    def test_rejects_traversal_absolute_and_shell_chars(self):
        for bad in [
            "",
            "   ",
            "/etc/passwd",
            "/opt/smsly-hosting/docker-compose.prod.yml",
            "../docker-compose.prod.yml",
            "sub/../../etc/passwd",
            "foo\\docker-compose.yml",
            "docker-compose.yml;whoami",
            "docker-compose.yml\x00.txt",
        ]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_compose_file_path(bad)

    def test_resolve_compose_path_blocks_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "repo")
            os.makedirs(repo_dir)
            resolved = resolve_compose_path(repo_dir, "sub/compose.yml")
            self.assertTrue(resolved.startswith(os.path.realpath(repo_dir) + os.sep))

            with self.assertRaises(ValueError):
                resolve_compose_path(repo_dir, "../outside.yml")

    def test_serializer_validates_compose_fields(self):
        ser = ServiceSerializer()
        self.assertEqual(ser.validate_compose_file("deploy/compose.yml"), "deploy/compose.yml")
        with self.assertRaises(serializers.ValidationError):
            ser.validate_compose_file("../../docker-compose.prod.yml")
        with self.assertRaises(serializers.ValidationError):
            ser.validate_compose_file("/opt/smsly-hosting/docker-compose.prod.yml")

        self.assertEqual(ser.validate_compose_main_service("api-v1"), "api-v1")
        with self.assertRaises(serializers.ValidationError):
            ser.validate_compose_main_service("web; id")
