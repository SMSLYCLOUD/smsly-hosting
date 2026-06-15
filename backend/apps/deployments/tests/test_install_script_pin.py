# pylint: disable=invalid-name
"""Tests for SEC (Issue 76): _load_install_script enforces a pinned SHA-256."""
import os
import tempfile
from unittest.mock import patch, mock_open

from django.test import SimpleTestCase

from apps.deployments.services import provisioner
from apps.deployments.services.provisioner import (
    EXPECTED_INSTALL_SCRIPT_SHA256,
    _load_install_script,
)


class InstallScriptPinTests(SimpleTestCase):
    """The local candidate path must verify against the embedded pin."""

    def setUp(self):
        # Stub out a local install.sh in /tmp and patch candidate paths
        self.tmpdir = tempfile.mkdtemp()
        self.local_script = os.path.join(self.tmpdir, "install.sh")
        with open(self.local_script, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho hi\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patched_load(self, candidate_path):
        return _load_install_script

    def test_local_matching_sha_returns_content(self):
        from apps.deployments.services import provisioner as prov
        script_bytes = open(self.local_script, "rb").read()
        # Override the candidate list to point at our temp file
        original_candidates = prov.os.path.abspath
        with patch(
            "apps.deployments.services.provisioner.os.path.abspath",
            side_effect=lambda p: self.local_script if "install.sh" in p else original_candidates(p),
        ), patch(
            "apps.deployments.services.provisioner.os.path.isfile",
            return_value=True,
        ), patch(
            "builtins.open",
            mock_open(read_data=script_bytes.decode("utf-8")),
        ):
            import hashlib
            actual_sha = hashlib.sha256(script_bytes).hexdigest()
            with patch.object(prov, "EXPECTED_INSTALL_SCRIPT_SHA256", actual_sha):
                content, source = _load_install_script()
        self.assertTrue(content.startswith("#!/bin/sh"))
        self.assertTrue(source.startswith("local:"))

    def test_mismatched_sha_raises(self):
        from apps.deployments.services import provisioner as prov
        script_bytes = open(self.local_script, "rb").read()
        with patch(
            "apps.deployments.services.provisioner.os.path.isfile",
            return_value=True,
        ), patch(
            "apps.deployments.services.provisioner.os.path.abspath",
            return_value=self.local_script,
        ), patch(
            "builtins.open",
            mock_open(read_data=script_bytes.decode("utf-8")),
        ):
            wrong_sha = "0" * 64
            with patch.object(prov, "EXPECTED_INSTALL_SCRIPT_SHA256", wrong_sha):
                with self.assertRaises(ValueError) as ctx:
                    _load_install_script()
        self.assertIn("SHA-256 mismatch", str(ctx.exception))

    def test_constant_is_a_64_char_hex(self):
        """The pin must be a 64-character lowercase hex digest."""
        self.assertEqual(len(EXPECTED_INSTALL_SCRIPT_SHA256), 64)
        int(EXPECTED_INSTALL_SCRIPT_SHA256, 16)  # raises if not hex
