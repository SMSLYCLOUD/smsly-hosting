# pylint: disable=invalid-name
import io
import logging
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.maintenance import AddonMaintenanceService
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


SENSITIVE_LITERAL = "S3CR3T-OLD-PASSWORD-DO-NOT-LOG-99"
SAFE_FRAGMENT = "rotation failed for addon"


class Finding99RotateCredentialsRedactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="redact99",
            email="redact99@test.com",
            password="testpass123",
        )
        self.service = Service.objects.create(
            name="redact-svc-99", owner=self.user,
        )
        self.addon = Addon.objects.create(
            service=self.service,
            name="pg-redact",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url="postgres://user:pass@localhost:5432/appdb",
        )

    def _capture_rotation(self):
        from unittest.mock import patch

        maintenance = AddonMaintenanceService(self.addon)
        with patch.object(
            maintenance.proxy,
            "get_connection",
            side_effect=OSError("connection refused"),
        ):
            result = maintenance.rotate_credentials()

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("apps.addons.services.maintenance")
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            with patch.object(
                maintenance.proxy,
                "get_connection",
                side_effect=OSError("connection refused"),
            ):
                maintenance.rotate_credentials()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        return result, log_stream.getvalue()

    def test_rotate_credentials_does_not_emit_old_secret_to_logs(self):
        result, captured = self._capture_rotation()
        self.assertEqual(result.get("status"), "failed")
        self.assertNotIn(
            SENSITIVE_LITERAL,
            captured,
            f"rotate_credentials leaked a secret into logs: {captured!r}",
        )
        self.assertNotIn(
            SENSITIVE_LITERAL,
            str(result),
            f"rotate_credentials leaked a secret into result: {result!r}",
        )

    def test_rotate_credentials_log_message_is_redact_safe(self):
        _, captured = self._capture_rotation()
        self.assertNotIn(SENSITIVE_LITERAL, captured)
        if captured.strip():
            self.assertIn(SAFE_FRAGMENT, captured.lower())
        self.assertNotRegex(
            captured,
            re.compile(r"(?i)password\s*[=:]\s*[\S]+"),
            "rotate_credentials must not log password=value pairs.",
        )
