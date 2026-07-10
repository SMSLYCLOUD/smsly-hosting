# pylint: disable=invalid-name
"""Tests for install script SHA-256 verification."""
import hashlib
import os
import tempfile
from unittest.mock import mock_open, patch

from django.test import SimpleTestCase

from apps.deployments.services.provisioner import _load_install_script


class InstallScriptVerificationTests(SimpleTestCase):
    """Verify _load_install_script enforces SHA-256 checks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.local_script = os.path.join(self.tmpdir, "install.sh")
        with open(self.local_script, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho hi\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_local_script_auto_calculates_sha(self):
        """Local script with no env var still passes via auto-calculated SHA."""
        script_bytes = open(self.local_script, "rb").read()
        with patch(
            "apps.deployments.services.provisioner.os.path.abspath",
            side_effect=lambda p: self.local_script if "install.sh" in p else p,
        ), patch(
            "apps.deployments.services.provisioner.os.path.isfile",
            return_value=True,
        ), patch(
            "builtins.open",
            mock_open(read_data=script_bytes.decode("utf-8")),
        ):
            content, source = _load_install_script()
        self.assertTrue(content.startswith("#!/bin/sh"))
        self.assertTrue(source.startswith("local:"))

    def test_url_fetch_without_sha_raises(self):
        """Downloading from URL without SMSLY_INSTALL_SCRIPT_SHA256 raises ValueError."""
        with patch(
            "apps.deployments.services.provisioner.os.path.isfile",
            return_value=False,
        ), patch(
            "apps.deployments.services.provisioner.os.environ.get",
            side_effect=lambda k, d="": "" if k == "SMSLY_INSTALL_SCRIPT_SHA256" else d,
        ), patch(
            "apps.deployments.services.provisioner.requests.get",
            return_value=mock_open(read_data="#!/bin/sh\necho hi\n")(),
        ):
            with self.assertRaises(ValueError) as ctx:
                _load_install_script()
        self.assertIn("Refusing to execute an unverified script", str(ctx.exception))

    def test_url_fetch_with_correct_sha_passes(self):
        """Downloading from URL with correct SHA passes."""
        script_content = "#!/bin/sh\necho hi\n"
        correct_sha = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
        with patch(
            "apps.deployments.services.provisioner.os.path.isfile",
            return_value=False,
        ), patch(
            "apps.deployments.services.provisioner.os.environ.get",
            side_effect=lambda k, d="": correct_sha if k == "SMSLY_INSTALL_SCRIPT_SHA256" else d,
        ), patch(
            "apps.deployments.services.provisioner.requests.get",
            return_value=mock_open(read_data=script_content)(),
        ):
            content, source = _load_install_script()
        self.assertEqual(content, script_content)
        self.assertTrue(source.startswith("url:"))

    def test_url_fetch_with_wrong_sha_raises(self):
        """Downloading from URL with wrong SHA raises ValueError."""
        script_content = "#!/bin/sh\necho hi\n"
        wrong_sha = "0" * 64
        with patch(
            "apps.deployments.services.provisioner.os.path.isfile",
            return_value=False,
        ), patch(
            "apps.deployments.services.provisioner.os.environ.get",
            side_effect=lambda k, d="": wrong_sha if k == "SMSLY_INSTALL_SCRIPT_SHA256" else d,
        ), patch(
            "apps.deployments.services.provisioner.requests.get",
            return_value=mock_open(read_data=script_content)(),
        ):
            with self.assertRaises(ValueError) as ctx:
                _load_install_script()
        self.assertIn("checksum mismatch", str(ctx.exception))
