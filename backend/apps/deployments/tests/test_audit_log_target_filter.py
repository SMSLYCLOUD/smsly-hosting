# pylint: disable=invalid-name
"""Tests for Issue 104: AuditLogViewSet.get_queryset includes user-service logs."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Service
from apps.deployments.models.audit import AuditLog

User = get_user_model()


class AuditLogTargetFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="aud-target", password="p",
        )
        self.other = User.objects.create_user(
            username="aud-other", password="p",
        )
        self.service = Service.objects.create(
            name="aud-svc", owner=self.user,
        )
        self.other_service = Service.objects.create(
            name="aud-other-svc", owner=self.other,
        )
        self.client = APIClient()

    def _make_log(self, actor, target, action="HEALTH_WEBHOOK_APPLIED", user=None):
        return AuditLog.objects.create(
            actor=actor,
            target=target,
            action=action,
            user=user,
            metadata={"service_id": target.split(":", 1)[1].strip() if "Service" in target else ""},
        )

    def test_user_actor_log_visible(self):
        self._make_log(self.user.get_username(), target="Service:foo")
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.get("/api/v1/audit-logs/")
        actions = [r["action"] for r in resp.data.get("results", resp.data)]
        self.assertIn("HEALTH_WEBHOOK_APPLIED", actions)

    def test_system_actor_targeting_user_service_is_visible(self):
        target = f"Service:{self.service.id}"
        self._make_log("AI_REPORTER", target=target, action="AI_REPORT")
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.get("/api/v1/audit-logs/")
        actions = [r["action"] for r in resp.data.get("results", resp.data)]
        self.assertIn("AI_REPORT", actions)

    def test_system_actor_targeting_other_service_not_visible(self):
        target = f"Service:{self.other_service.id}"
        self._make_log("AI_REPORTER", target=target, action="AI_REPORT_OTHER")
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.get("/api/v1/audit-logs/")
        actions = [r["action"] for r in resp.data.get("results", resp.data)]
        self.assertNotIn("AI_REPORT_OTHER", actions)

    def test_target_with_space_in_service_id_format_included(self):
        target = f"Service: {self.service.id}"
        self._make_log("AI_REPORTER", target=target, action="AI_REPORT_B")
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.get("/api/v1/audit-logs/")
        actions = [r["action"] for r in resp.data.get("results", resp.data)]
        self.assertIn("AI_REPORT_B", actions)

    def test_superuser_sees_all(self):
        self._make_log("system", target="Service:foo", action="ANY_ACTION")
        superuser = User.objects.create_superuser(
            username="aud-super", password="p",
        )
        client = APIClient()
        client.force_authenticate(user=superuser)
        resp = client.get("/api/v1/audit-logs/")
        actions = [r["action"] for r in resp.data.get("results", resp.data)]
        self.assertIn("ANY_ACTION", actions)
