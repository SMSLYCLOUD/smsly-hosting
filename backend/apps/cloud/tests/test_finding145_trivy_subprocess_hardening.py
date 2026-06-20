# pylint: disable=invalid-name
"""Regression tests for Finding #145 (trivy subprocess hardening).

The ``NixpacksBuilder.scan_image`` call to ``trivy`` previously had
no timeout and silently returned a fake ``{"error": ...}`` payload
on a non-zero return code. The fix:

  * passes ``timeout=300`` so a hung trivy subprocess cannot block
    the Celery worker forever;
  * captures stderr via ``capture_output=True``;
  * raises ``RuntimeError`` on a non-zero return code so callers
    (and the audit log) see the real failure.

The tests below pin all three behaviors and verify the argv is a
list, not a string (which would be unsafe under ``shell=True``).
"""
import json
from unittest.mock import MagicMock, patch

from apps.cloud.services.builder import NixpacksBuilder
from django.test import SimpleTestCase


class TrivySubprocessHardeningTests(SimpleTestCase):
    def _completed_process(self, returncode=0, stdout="{}", stderr=""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    @patch("apps.cloud.services.builder.subprocess.run")
    def test_trivy_called_with_list_argv(self, mock_run):
        mock_run.return_value = self._completed_process(
            stdout=json.dumps({"Results": []}),
        )
        NixpacksBuilder.scan_image("example/image:tag")
        args, _kwargs = mock_run.call_args
        self.assertTrue(args, "subprocess.run should be called positionally")
        argv = args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], "trivy")
        self.assertIn("image", argv)
        self.assertEqual(argv[-1], "example/image:tag")

    @patch("apps.cloud.services.builder.subprocess.run")
    def test_trivy_called_with_timeout_300(self, mock_run):
        mock_run.return_value = self._completed_process(
            stdout=json.dumps({"Results": []}),
        )
        NixpacksBuilder.scan_image("example/image:tag")
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("timeout"), 300)

    @patch("apps.cloud.services.builder.subprocess.run")
    def test_trivy_captures_stderr(self, mock_run):
        mock_run.return_value = self._completed_process(
            stdout=json.dumps({"Results": []}),
        )
        NixpacksBuilder.scan_image("example/image:tag")
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("capture_output"))

    @patch("apps.cloud.services.builder.subprocess.run")
    def test_trivy_nonzero_return_raises_runtime_error(self, mock_run):
        mock_run.return_value = self._completed_process(
            returncode=1,
            stdout="",
            stderr="trivy: invalid flag",
        )
        with self.assertRaises(RuntimeError) as ctx:
            NixpacksBuilder.scan_image("example/image:tag")
        self.assertIn("trivy", str(ctx.exception).lower())

    @patch("apps.cloud.services.builder.subprocess.run")
    def test_trivy_binary_missing_returns_unscanned(self, mock_run):
        mock_run.side_effect = FileNotFoundError("trivy")
        result = NixpacksBuilder.scan_image("example/image:tag")
        self.assertEqual(result.get("status"), "unscanned")
        self.assertEqual(result.get("reason"), "trivy_missing")
