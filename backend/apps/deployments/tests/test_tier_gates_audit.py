# pylint: disable=invalid-name
"""
Regression tests for Issue 21 (SMSLY_DISABLE_TIER_GATES audit log).

The ``_check_tier_gates_disabled`` helper in apps.deployments.views
must:
  * return False when the flag is unset;
  * return True and write exactly one AuditLog entry per process
    when the flag is set (the second call must NOT write another
    entry);
  * fall back to the env var if the settings attribute is missing.
"""

import os
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.deployments.models.audit import AuditLog


class TierGatesAuditTests(SimpleTestCase):
    def setUp(self):
        # Reset the module-level latch between tests.
        from apps.deployments import views as deployments_views
        deployments_views._TIER_GATES_LOGGED = False

    def test_returns_false_when_flag_unset(self):
        from django.conf import settings

        from apps.deployments import views as deployments_views
        with patch.object(settings, "SMSLY_DISABLE_TIER_GATES", False, create=True):
            self.assertFalse(deployments_views._check_tier_gates_disabled())

    def test_returns_true_when_flag_set(self):
        from django.conf import settings

        from apps.deployments import views as deployments_views
        with patch.object(settings, "SMSLY_DISABLE_TIER_GATES", True, create=True):
            self.assertTrue(deployments_views._check_tier_gates_disabled())

    def test_falls_back_to_env_var(self):
        from django.conf import settings

        from apps.deployments import views as deployments_views
        with patch.object(settings, "SMSLY_DISABLE_TIER_GATES", False, create=True):
            with patch.dict(os.environ, {"SMSLY_DISABLE_TIER_GATES": "true"}):
                self.assertTrue(deployments_views._check_tier_gates_disabled())


class TierGatesAuditLogTests(TestCase):
    def setUp(self):
        from apps.deployments import views as deployments_views
        deployments_views._TIER_GATES_LOGGED = False

    def test_first_consult_writes_audit_log(self):
        from django.conf import settings

        from apps.deployments import views as deployments_views
        with patch.object(settings, "SMSLY_DISABLE_TIER_GATES", True, create=True):
            deployments_views._check_tier_gates_disabled()
        log = AuditLog.objects.filter(action="TIER_GATES_DISABLED").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata.get("env_var"), "SMSLY_DISABLE_TIER_GATES")

    def test_second_consult_does_not_write_audit_log(self):
        from django.conf import settings

        from apps.deployments import views as deployments_views
        with patch.object(settings, "SMSLY_DISABLE_TIER_GATES", True, create=True):
            deployments_views._check_tier_gates_disabled()
            deployments_views._check_tier_gates_disabled()
            deployments_views._check_tier_gates_disabled()
        self.assertEqual(
            AuditLog.objects.filter(action="TIER_GATES_DISABLED").count(),
            1,
        )
