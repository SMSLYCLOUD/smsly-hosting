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
SAFE_FRAGMENT = "rotation requested for addon"


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

    def test_rotate_credentials_does_not_emit_old_secret_to_logs(self):
        maintenance = AddonMaintenanceService(self.addon)

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("apps.addons.services.maintenance")
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            result = maintenance.rotate_credentials()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertEqual(result.get("status"), "not_implemented")
        captured = log_stream.getvalue()
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
        maintenance = AddonMaintenanceService(self.addon)

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("apps.addons.services.maintenance")
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            maintenance.rotate_credentials()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        captured = log_stream.getvalue()
        self.assertNotIn(SENSITIVE_LITERAL, captured)
        if captured.strip():
            self.assertIn(SAFE_FRAGMENT, captured.lower())
        self.assertNotRegex(
            captured,
            re.compile(r"(?i)password\s*[=:]\s*[\S]+"),
            "rotate_credentials must not log password=value pairs.",
        )
