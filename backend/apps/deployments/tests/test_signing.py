"""Signing skip diagnostics: a skip must say WHICH gate failed."""
import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.deployments.services.pipeline.signing import SigningMixin


def _mixin():
    m = SigningMixin()
    m.image_name = "smsly/svc:abc1234"
    m.deployment = MagicMock()
    m._check_cancellation = MagicMock()
    return m


def _run_sign(m):
    with patch.object(
        SigningMixin, "_cosign_enabled", return_value=(True, False)
    ), patch(
        "apps.deployments.services.pipeline.signing.find_binary",
        return_value="/usr/local/bin/cosign",
    ), patch.object(
        SigningMixin, "_is_local_registry", return_value=True
    ), patch(
        "apps.deployments.services.pipeline.signing.append_log"
    ) as append_log, patch(
        "apps.deployments.services.pipeline.signing.update_stage"
    ) as update_stage:
        m._sign_image()
    return append_log, update_stage


class SigningSkipReasonTests(SimpleTestCase):
    def test_missing_env_names_itself(self):
        with patch.dict(os.environ):
            os.environ.pop("COSIGN_PRIVATE_KEY_PATH", None)
            os.environ.pop("COSIGN_KEY", None)
            append_log, update_stage = _run_sign(_mixin())
        message = append_log.call_args[0][1]
        self.assertIn("Reason:", message)
        self.assertIn("not set", message)
        self.assertEqual(update_stage.call_args[0][1:], ("Sign", "skipped"))

    def test_missing_file_names_itself(self):
        with patch.dict(os.environ, {"COSIGN_PRIVATE_KEY_PATH": "/nonexistent/cosign.key"}):
            os.environ.pop("COSIGN_KEY", None)
            append_log, _ = _run_sign(_mixin())
        message = append_log.call_args[0][1]
        self.assertIn("Reason:", message)
        self.assertIn("missing", message)
